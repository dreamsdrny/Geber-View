//! Stable C boundary for the experimental Qt viewer; no Python runtime.
use gerber_core::preview::parse_preview;
use std::ffi::c_char;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::ptr;

#[repr(C)]
#[derive(Clone, Copy)]
pub struct ViewerStroke {
    pub x1: f64,
    pub y1: f64,
    pub x2: f64,
    pub y2: f64,
    pub diameter: f64,
    pub flash: u32,
}

pub struct ViewerLayer {
    strokes: Vec<ViewerStroke>,
}

unsafe fn write_error(buffer: *mut c_char, capacity: usize, message: &str) {
    if buffer.is_null() || capacity == 0 {
        return;
    }
    let bytes = message.as_bytes();
    let n = bytes.len().min(capacity - 1);
    ptr::copy_nonoverlapping(bytes.as_ptr(), buffer.cast::<u8>(), n);
    *buffer.add(n) = 0;
}

/// Pointers passed by the caller must remain valid for their stated lengths.
/// Return 0 on success, 1 for unsupported input, 2 for invalid arguments, 3 on panic.
#[no_mangle]
pub unsafe extern "C" fn viewer_parse_gerber(
    data: *const u8,
    len: usize,
    output: *mut *mut ViewerLayer,
    error: *mut c_char,
    error_capacity: usize,
) -> i32 {
    if output.is_null() {
        return 2;
    }
    *output = ptr::null_mut();
    write_error(error, error_capacity, "");
    if data.is_null() || len == 0 || len > 128 * 1024 * 1024 {
        write_error(error, error_capacity, "文件为空或超过 128 MiB 安全上限");
        return 2;
    }
    match catch_unwind(AssertUnwindSafe(|| {
        let input = std::slice::from_raw_parts(data, len);
        parse_preview(input).map(|layer| {
            let strokes = layer
                .strokes
                .into_iter()
                .map(|s| ViewerStroke {
                    x1: s.x1,
                    y1: s.y1,
                    x2: s.x2,
                    y2: s.y2,
                    diameter: s.diameter,
                    flash: u32::from(s.flash),
                })
                .collect();
            Box::into_raw(Box::new(ViewerLayer { strokes }))
        })
    })) {
        Ok(Ok(layer)) => {
            *output = layer;
            0
        }
        Ok(Err(message)) => {
            write_error(error, error_capacity, message);
            1
        }
        Err(_) => {
            write_error(error, error_capacity, "解析核心发生内部错误");
            3
        }
    }
}

/// The returned array is valid until the layer is freed; caller must not modify it.
#[no_mangle]
pub unsafe extern "C" fn viewer_layer_strokes(
    layer: *const ViewerLayer,
    count: *mut usize,
) -> *const ViewerStroke {
    if layer.is_null() || count.is_null() {
        return ptr::null();
    }
    *count = (*layer).strokes.len();
    (*layer).strokes.as_ptr()
}

#[no_mangle]
pub unsafe extern "C" fn viewer_layer_free(layer: *mut ViewerLayer) {
    if !layer.is_null() {
        drop(Box::from_raw(layer));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ffi_valid_and_invalid() {
        let data = b"%FSLAX34Y34*%%MOMM*%%ADD10C,1*%D10*X10000Y10000D03*M02*";
        let mut layer = ptr::null_mut();
        let mut error = [0_i8; 256];
        unsafe {
            assert_eq!(
                viewer_parse_gerber(
                    data.as_ptr(),
                    data.len(),
                    &mut layer,
                    error.as_mut_ptr(),
                    error.len()
                ),
                0
            );
            assert!(!layer.is_null());
            let mut count = 0;
            assert!(!viewer_layer_strokes(layer, &mut count).is_null());
            assert_eq!(count, 1);
            viewer_layer_free(layer);
            layer = ptr::null_mut();
            assert_eq!(
                viewer_parse_gerber(
                    b"bad".as_ptr(),
                    3,
                    &mut layer,
                    error.as_mut_ptr(),
                    error.len()
                ),
                1
            );
            assert!(layer.is_null());
            assert_ne!(error[0], 0);
            assert_eq!(
                viewer_parse_gerber(ptr::null(), 1, &mut layer, error.as_mut_ptr(), error.len()),
                2
            );
        }
    }
}
