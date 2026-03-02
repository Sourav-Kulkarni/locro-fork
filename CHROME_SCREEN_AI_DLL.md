# Reverse-Engineering `chrome_screen_ai.dll`

This document describes the internal interface of Google's `chrome_screen_ai.dll` -- the on-device OCR library shipped as a Chrome component. The DLL can be loaded and called directly from Python (or any language with FFI), bypassing Chrome entirely.

## Table of Contents

- [Background](#background)
- [Locating the DLL and Models](#locating-the-dll-and-models)
- [Exported Functions](#exported-functions)
- [Calling Sequence](#calling-sequence)
- [The SkBitmap Problem](#the-skbitmap-problem)
- [SkBitmap Memory Layout](#skbitmap-memory-layout)
- [How the Layout Was Determined](#how-the-layout-was-determined)
- [The SkPixelRef Workaround](#the-skpixelref-workaround)
- [Output Format (VisualAnnotation Protobuf)](#output-format-visualannotation-protobuf)
- [Constraints and Limitations](#constraints-and-limitations)
- [Chromium Source References](#chromium-source-references)

---

## Background

Chrome ships an on-device OCR engine called "Screen AI" (internally codenamed "gOCR"). It's distributed as a Chrome component -- a separately downloadable package managed by Chrome's component updater. The package contains:

- `chrome_screen_ai.dll` (~33 MB) -- the main library containing the OCR inference engine
- TFLite model files for text detection, line recognition, layout analysis, and language identification
- Config protobufs and label maps for 20+ scripts/languages

Inside Chrome, this library is loaded by `services/screen_ai/screen_ai_library_wrapper_impl.cc`, which resolves the exported functions at runtime and wraps them in a C++ class. The key insight is that the DLL exports plain C-linkage functions (no name mangling), making it callable from any language.

## Locating the DLL and Models

On Windows, the component lives under Chrome's user data directory:

```
%LOCALAPPDATA%\Google\Chrome\User Data\screen_ai\<version>\
```

For example:

```
C:\Users\<user>\AppData\Local\Google\Chrome\User Data\screen_ai\140.20\
```

The version directory contains the DLL alongside all its model files. The directory structure:

```
140.20/
  chrome_screen_ai.dll                              (33 MB - main library)
  gocr_mobile_chrome_multiscript_2024_q4_engine.binarypb   (engine config)
  manifest.json                                     (component metadata)
  screen2x_model.tflite                             (main content extraction model)
  screen2x_config.pbtxt
  aksara/
    aksara_page_layout_analysis_rpn_gro_2024_q4.binarypb   (layout analysis config)
  gocr/
    gocr_models/
      detection/
        gocr_group_rpn_text_detection_model_2024_q4.tflite (2 MB - text detector)
        gocr_group_rpn_text_detection_config_2024_q4_chrome.binarypb
      line_recognition_mobile_convnext320_omni/
        gocr_mobile_und.tflite                      (3 MB - universal line recognizer)
        gocr_mobile_und_label_map.pb
        tflite_langid.tflite                        (language identification)
        arab.tflite, cyrl.tflite, grek.tflite, ...  (script-specific recognizers)
        arab_lm.fst, arab_lm.syms, ...              (language models + symbol tables)
    layout/
      cluster_sort/
        model_v2.tflite                             (layout ordering model)
```

**Important**: The DLL does NOT read these files from disk itself. Instead, it asks the host process to provide file contents via callbacks (see `SetFileContentFunctions` below). This is how Chrome controls file access in its sandboxed architecture.

## Exported Functions

The DLL exports 11 functions with C linkage. They were discovered by probing known names from the Chromium source code:

| Export Name | C Signature | Purpose |
|---|---|---|
| `GetLibraryVersion` | `void(uint32_t& major, uint32_t& minor)` | Returns the library version (e.g., 140, 20) |
| `SetFileContentFunctions` | `void(get_size_fn, get_content_fn)` | Registers callbacks for reading model files |
| `EnableDebugMode` | `void()` | Enables debug logging (writes i/o protos to temp folder) |
| `InitOCRUsingCallback` | `bool()` | Initializes the OCR pipeline (loads models via the callbacks) |
| `SetOCRLightMode` | `void(bool enabled)` | Toggles the light (smaller/faster) model |
| `GetMaxImageDimension` | `uint32_t()` | Returns max image dimension (currently 2048) |
| `PerformOCR` | `char*(const SkBitmap& image, uint32_t& output_length)` | Runs OCR; returns serialized protobuf |
| `FreeLibraryAllocatedCharArray` | `void(char* memory)` | Frees memory returned by `PerformOCR` |
| `FreeLibraryAllocatedInt32Array` | `void(int32_t* memory)` | Frees memory returned by `ExtractMainContent` |
| `InitMainContentExtractionUsingCallback` | `bool()` | Initializes the main-content-extraction pipeline |
| `ExtractMainContent` | `int32_t*(const char* data, uint32_t len, uint32_t& count)` | Extracts main content node IDs from an accessibility tree |
| `SetLogger` | `void(logger_fn)` | (ChromeOS only) Registers a logging callback |

### Callback Signatures for `SetFileContentFunctions`

The DLL reads model files by calling two host-provided functions:

```c
// Returns the file size in bytes. The DLL calls this first to allocate a buffer.
typedef uint32_t (*GetFileContentSizeFn)(const char* relative_file_path);

// Copies the file content into the provided buffer.
typedef void (*GetFileContentFn)(
    const char* relative_file_path,
    uint32_t buffer_size,
    char* buffer
);
```

The `relative_file_path` is relative to the component directory. For example, the DLL will request paths like:
- `gocr_mobile_chrome_multiscript_2024_q4_engine.binarypb`
- `gocr/gocr_models/detection/gocr_group_rpn_text_detection_model_2024_q4.tflite`
- `gocr/gocr_models/line_recognition_mobile_convnext320_omni/gocr_mobile_und.tflite`

## Calling Sequence

The correct order of operations:

```
1. LoadLibrary("chrome_screen_ai.dll")
2. GetLibraryVersion(&major, &minor)           -- optional, verify version
3. SetFileContentFunctions(size_cb, content_cb) -- MUST be called before Init
4. EnableDebugMode()                            -- optional
5. InitOCRUsingCallback()                       -- loads all models; returns bool
6. GetMaxImageDimension()                       -- query the limit (2048)
7. PerformOCR(bitmap, &output_length)           -- run OCR; returns char*
8. ... use the protobuf data ...
9. FreeLibraryAllocatedCharArray(ptr)            -- free the returned buffer
```

Steps 7-9 can be repeated for multiple images without re-initializing. The `InitOCRUsingCallback` step is expensive (~50ms) as it loads multiple TFLite models and initializes the MediaPipe pipeline. Subsequent `PerformOCR` calls run in ~1-2 seconds per image.

## The SkBitmap Problem

The biggest challenge in calling the DLL from Python is that `PerformOCR` takes a `const SkBitmap&` -- a reference to a Skia C++ class. Skia is Google's 2D graphics library, and `SkBitmap` is its bitmap container. The DLL was compiled against a specific version of Skia, and expects the exact in-memory layout of that version's `SkBitmap`.

Since `const SkBitmap&` in x86-64 calling convention is just a pointer, we can construct the correct byte layout in Python and pass a pointer to it. The DLL will read the struct fields at their expected offsets.

## SkBitmap Memory Layout

The layout was determined for the DLL from Chrome component version **140.20** on **64-bit Windows**. Other versions may differ.

### SkBitmap (total: 49 bytes + padding = 56 bytes)

```
Offset  Size  Type                Field
──────  ────  ──────────────────  ────────────────────────
  0       8   void*               fPixelRef    (sk_sp<SkPixelRef>)
  8       8   void*               fPixmap.fPixels
 16       8   size_t              fPixmap.fRowBytes
 24       8   void*               fPixmap.fInfo.fColorSpace  (sk_sp<SkColorSpace>)
 32       4   int32               fPixmap.fInfo.fColorType   (SkColorType enum)
 36       4   int32               fPixmap.fInfo.fAlphaType   (SkAlphaType enum)
 40       4   int32               fPixmap.fInfo.fWidth
 44       4   int32               fPixmap.fInfo.fHeight
 48       1   uint8               fFlags
```

### SkImageInfo Field Order

**Critical**: The field order inside `SkImageInfo` for this DLL version is:

```
fColorSpace, fColorType, fAlphaType, fWidth, fHeight
```

This is different from what you might expect reading the current Skia source, where `SkISize fDimensions` (width, height) appears before the color/alpha types. The DLL's compiled layout puts color type at **offset 8** within `SkImageInfo` (= offset 32 within `SkBitmap`).

### SkColorType Values

| Value | Name | Notes |
|---|---|---|
| 0 | `kUnknown_SkColorType` | |
| 4 | `kRGBA_8888_SkColorType` | |
| 6 | `kBGRA_8888_SkColorType` | This is `kN32` on Windows. **Use this.** |

### SkAlphaType Values

| Value | Name |
|---|---|
| 1 | `kOpaque_SkAlphaType` |
| 2 | `kPremul_SkAlphaType` |

### Values to Use

- `fColorSpace` = `0` (null = sRGB)
- `fColorType` = `6` (kBGRA_8888)
- `fAlphaType` = `2` (kPremul)
- `fPixels` = pointer to raw BGRA pixel data
- `fRowBytes` = `width * 4`
- `fPixelRef` = pointer to a valid (fake) SkPixelRef (see below)
- `fFlags` = `0`

## How the Layout Was Determined

The struct layout was reverse-engineered through empirical testing -- feeding known values and observing the DLL's error messages. No disassembly was required.

### Step 1: Initial Guess from Chromium Source

The Chromium source at `services/screen_ai/screen_ai_library_wrapper_impl.h` declares the function pointer types:

```cpp
typedef char* (*PerformOcrFn)(
    const SkBitmap& bitmap,
    uint32_t& serialized_visual_annotation_length);
```

This confirmed that `PerformOCR` takes a pointer to an `SkBitmap` and an out-parameter for the output length.

The initial struct layout was guessed from Skia's public headers (`SkBitmap.h`, `SkPixmap.h`, `SkImageInfo.h`):

```
SkBitmap: { sk_sp<SkPixelRef>, SkPixmap { fPixels, fRowBytes, SkImageInfo { fColorSpace, fWidth, fHeight, fColorType, fAlphaType } }, fFlags }
```

### Step 2: Color Type Error Reveals Field Offset

With the initial layout, `PerformOCR` printed:

```
Unsupported color type: 1377
```

The value 1377 was actually our `fWidth` (the image was 1377 pixels wide). This meant the DLL was reading offset 32 in the struct and interpreting it as `fColorType`. In our initial layout, offset 32 held `fWidth`, while `fColorType` was at offset 40.

This told us the DLL expects `fColorType` at `SkImageInfo` offset 8 (= `SkBitmap` offset 32). The only layout that achieves this is putting `fColorSpace` first (8 bytes), then `fColorType` (4 bytes) at offset 8:

```
SkImageInfo: { fColorSpace(8), fColorType(4), fAlphaType(4), fWidth(4), fHeight(4) }
```

### Step 3: Image Size Error Confirms Dimension Offsets

After fixing the color type, the DLL printed:

```
Unsupported image size.
```

This occurred even for small images (100x100). With the corrected layout, `fWidth` and `fHeight` are at `SkImageInfo` offsets 16 and 20 (= `SkBitmap` offsets 40 and 44). Our previous layout had `fColorSpace` (= 0, null) at offset 40, so the DLL read `width = 0, height = 0`.

Swapping to `{ fColorSpace, fColorType, fAlphaType, fWidth, fHeight }` placed the actual dimensions at offset 40-44, fixing the issue.

### Step 4: SkPixelRef Null Check

Even with correct dimensions, the DLL still returned "Unsupported image size." for any input. Studying the Skia source revealed that `SkBitmap::drawsNothing()` checks `isNull()`, which tests `fPixelRef != nullptr`. Setting `fPixelRef` to a pointer to a fake `SkPixelRef` struct resolved this.

### Summary of Iterations

| Attempt | Error | Diagnosis | Fix |
|---|---|---|---|
| 1 | `Unsupported color type: 1377` | DLL reads offset 32 as color type; we had width there | Reorder SkImageInfo: colorspace, colortype, alphatype, width, height |
| 2 | `Unsupported image size.` | DLL reads offsets 40-44 as width/height; we had colorspace (0) there | Same reorder as above placed correct values at 40-44 |
| 3 | `Unsupported image size.` (still) | `SkBitmap::isNull()` checks `fPixelRef != nullptr`; ours was null | Allocate a fake SkPixelRef and point fPixelRef to it |
| 4 | Success: 206,305 bytes of protobuf | Everything correct | -- |

## The SkPixelRef Workaround

`SkBitmap::isNull()` checks whether `fPixelRef` (an `sk_sp<SkPixelRef>`, which in memory is just a bare pointer) is non-null. However, `SkBitmap::getPixels()` returns `fPixmap.fPixels` directly, NOT through `fPixelRef`.

This means we need `fPixelRef` to be non-null (to pass the null check), but the DLL's actual pixel access goes through `fPixmap.fPixels`. We create a minimal fake `SkPixelRef`:

```
FakeSkPixelRef (104 bytes):
  Offset  Size  Field
  ──────  ────  ─────────
    0       8   vtable_ptr   -> points to an array of null function pointers
    8       4   refcount     = 1
   12       4   padding
   16       4   fWidth       = image width
   20       4   fHeight      = image height
   24       8   fPixels      = pointer to pixel data
   32       8   fRowBytes    = width * 4
   40      64   padding      (safety margin)
```

The vtable pointer must be non-null (it points to a zeroed array of function pointers). The DLL doesn't call virtual methods on `SkPixelRef` during `PerformOCR`, so the null function pointers are never dereferenced.

## Output Format (VisualAnnotation Protobuf)

`PerformOCR` returns a serialized `chrome_screen_ai.VisualAnnotation` protobuf. The proto definition is in the Chromium source at `services/screen_ai/proto/chrome_screen_ai.proto`.

### Top-Level Message

```protobuf
message VisualAnnotation {
  repeated LineBox lines = 2;
}
```

### LineBox (one per detected text line)

```protobuf
message LineBox {
  repeated WordBox words = 1;
  Rect bounding_box = 2;
  string utf8_string = 3;           // the full line text
  string language = 4;              // ISO 639-1 (e.g., "en") or 639-2
  int32 block_id = 5;               // groups lines into blocks
  Direction direction = 7;          // LTR, RTL, TTB
  ContentType content_type = 8;     // printed text, handwritten, image, etc.
  float confidence = 10;            // 0.0 to 1.0
  int32 paragraph_id = 11;          // groups lines into paragraphs within a block
}
```

### WordBox (one per word within a line)

```protobuf
message WordBox {
  repeated SymbolBox symbols = 1;
  Rect bounding_box = 2;
  string utf8_string = 3;
  string language = 5;
  float confidence = 15;
  // Also: color info, whitespace bounding box, etc.
}
```

### Rect (bounding box)

```protobuf
message Rect {
  int32 x = 1;      // top-left x
  int32 y = 2;      // top-left y
  int32 width = 3;
  int32 height = 4;
  float angle = 5;  // rotation in degrees (clockwise)
}
```

### ContentType Enum

| Value | Name |
|---|---|
| 0 | `PRINTED_TEXT` |
| 1 | `HANDWRITTEN_TEXT` |
| 2 | `IMAGE` |
| 3 | `LINE_DRAWING` |
| 4 | `SEPARATOR` |
| 5 | `UNREADABLE_TEXT` |
| 6 | `FORMULA` |
| 7 | `HANDWRITTEN_FORMULA` |
| 8 | `SIGNATURE` |

### Direction Enum

| Value | Name |
|---|---|
| 0 | `UNSPECIFIED` |
| 1 | `LEFT_TO_RIGHT` |
| 2 | `RIGHT_TO_LEFT` |
| 3 | `TOP_TO_BOTTOM` |

Coordinates in the bounding boxes are in pixels, relative to the image that was passed to `PerformOCR` (which may have been resized from the original).

## Constraints and Limitations

1. **Max image dimension**: Both width and height must be <= 2048 pixels (from `GetMaxImageDimension()`). Images larger than this must be downscaled before calling `PerformOCR`.

2. **Pixel format**: The DLL expects BGRA_8888 (`kN32_SkColorType` on Windows = value 6). RGBA or RGB images must be converted.

3. **Windows only**: The DLL is a Windows PE binary. On Linux/macOS, Chrome distributes a `.so`/`.dylib` with the same exports but potentially different struct layouts.

4. **Version coupling**: The SkBitmap layout is tied to the specific Skia version compiled into the DLL. A Chrome update that ships a new `chrome_screen_ai.dll` could change the struct layout and break the integration. The empirical layout determination process would need to be repeated.

5. **Thread safety**: The DLL spawns its own worker threads for TFLite inference. Multiple concurrent `PerformOCR` calls from the host are not tested and may not be safe.

6. **Memory management**: Buffers returned by `PerformOCR` must be freed with `FreeLibraryAllocatedCharArray`, not with `free()` or any host allocator. The DLL uses its own internal allocator.

7. **No incremental/streaming OCR**: Each `PerformOCR` call processes one complete image. For multi-page PDFs, render each page to an image and call `PerformOCR` separately.

## Chromium Source References

The Chromium source code provides the authoritative (if sometimes outdated) documentation for these interfaces:

- **DLL wrapper**: [`services/screen_ai/screen_ai_library_wrapper_impl.h`](https://chromium.googlesource.com/chromium/src/+/main/services/screen_ai/screen_ai_library_wrapper_impl.h) -- declares all function pointer typedefs
- **DLL wrapper impl**: [`services/screen_ai/screen_ai_library_wrapper_impl.cc`](https://chromium.googlesource.com/chromium/src/+/main/services/screen_ai/screen_ai_library_wrapper_impl.cc) -- shows calling conventions and which export names map to which typedefs
- **Protobuf schema**: [`services/screen_ai/proto/chrome_screen_ai.proto`](https://chromium.googlesource.com/chromium/src/+/main/services/screen_ai/proto/chrome_screen_ai.proto) -- full proto definition for `VisualAnnotation`
- **SkBitmap**: [`third_party/skia/include/core/SkBitmap.h`](https://skia.googlesource.com/skia/+/refs/heads/main/include/core/SkBitmap.h)
- **SkImageInfo**: [`third_party/skia/include/core/SkImageInfo.h`](https://skia.googlesource.com/skia/+/refs/heads/main/include/core/SkImageInfo.h)
