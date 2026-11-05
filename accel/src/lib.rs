// SagatVNC Viewer - optional native decoder loops
// Copyright (C) 2026 SagatVNC Viewer contributors
// Based on TigerVNC common/rfb/{Tight,ZRLE,Hextile,RRE}Decoder.cxx,
// Copyright (C) 2002-2005 RealVNC Ltd., Copyright 2004-2005 Cendio AB,
// Copyright 2009-2022 Pierre Ossman for Cendio AB, and TigerVNC
// contributors.
//
// This is free software; you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation; either version 2 of the License, or
// (at your option) any later version.

//! The decoder loops that are inherently sequential, so they can't be
//! expressed with Python's C-backed bytes operations: Tight's gradient
//! filter, ZRLE run-length tiles, Hextile and RRE subrectangles.
//!
//! Python (sagatvnc/rfb/accel.py) loads this with ctypes and keeps doing
//! the protocol parsing; these functions only turn bytes into pixels.
//! Every function checks its bounds and returns a negative error code
//! instead of panicking, and the Python fallback produces identical
//! output.

use std::slice;

/// Input ended before the tile/rectangle was complete.
pub const ERR_SHORT: isize = -1;
/// A run or subrectangle reaches outside its tile/rectangle.
pub const ERR_OUTSIDE: isize = -2;
/// A ZRLE palette index is not in the palette.
pub const ERR_INDEX: isize = -3;
/// Bad arguments (null pointers, sizes that don't fit).
pub const ERR_ARGS: isize = -4;

/// The API version Python checks before using the library.
#[no_mangle]
pub extern "C" fn sagatvnc_accel_version() -> u32 {
    1
}

/// Tight gradient filter for 24 bit colour
/// (TightDecoder::FilterGradient24): `inp` and `out` are `w * h * 3`
/// bytes of R,G,B.
#[no_mangle]
pub unsafe extern "C" fn tight_gradient(
    inp: *const u8,
    out: *mut u8,
    w: usize,
    h: usize,
) -> isize {
    let n = match w.checked_mul(h).and_then(|p| p.checked_mul(3)) {
        Some(n) => n,
        None => return ERR_ARGS,
    };
    if n == 0 {
        return 0;
    }
    if inp.is_null() || out.is_null() {
        return ERR_ARGS;
    }
    let inp = slice::from_raw_parts(inp, n);
    let out = slice::from_raw_parts_mut(out, n);
    gradient(inp, out, w, h);
    0
}

fn gradient(inp: &[u8], out: &mut [u8], w: usize, h: usize) {
    let stride = w * 3;
    let mut prev = vec![0u8; stride];
    for y in 0..h {
        let row_in = &inp[y * stride..(y + 1) * stride];
        let row_out = &mut out[y * stride..(y + 1) * stride];
        // First pixel in a row: the pixel above is the estimate
        for c in 0..3 {
            row_out[c] = row_in[c].wrapping_add(prev[c]);
        }
        for x in 1..w {
            for c in 0..3 {
                let i = x * 3 + c;
                let est = prev[i] as i32 + row_out[i - 3] as i32 - prev[i - 3] as i32;
                let est = est.clamp(0, 255) as u8;
                row_out[i] = row_in[i].wrapping_add(est);
            }
        }
        prev.copy_from_slice(row_out);
    }
}

/// Reads a ZRLE run length (1 + sum of bytes up to the first non-255).
fn run_length(data: &[u8], pos: &mut usize) -> Option<usize> {
    let mut run = 1usize;
    loop {
        let b = *data.get(*pos)?;
        *pos += 1;
        run += b as usize;
        if b != 255 {
            return Some(run);
        }
    }
}

/// A ZRLE run-length tile (ZRLEDecoder.cxx), from `data[pos..len]`.
///
/// `pal_size == 0`: plain RLE; `out` gets `total` pixels of `cp` bytes
/// (CPIXELs, converted to RGB by the caller).  Otherwise palette RLE;
/// `out` gets `total` one-byte palette indices.  Returns the position
/// after the tile, or a negative error code.
#[no_mangle]
pub unsafe extern "C" fn zrle_rle(
    data: *const u8,
    len: usize,
    pos: usize,
    total: usize,
    cp: usize,
    pal_size: usize,
    out: *mut u8,
) -> isize {
    if data.is_null() || out.is_null() || pos > len || cp == 0 || cp > 4 {
        return ERR_ARGS;
    }
    let data = slice::from_raw_parts(data, len);
    let width = if pal_size == 0 { cp } else { 1 };
    let out_len = match total.checked_mul(width) {
        Some(n) => n,
        None => return ERR_ARGS,
    };
    let out = slice::from_raw_parts_mut(out, out_len);
    match zrle_tile(data, pos, total, cp, pal_size, out) {
        Ok(p) => p as isize,
        Err(e) => e,
    }
}

fn zrle_tile(
    data: &[u8],
    mut pos: usize,
    total: usize,
    cp: usize,
    pal_size: usize,
    out: &mut [u8],
) -> Result<usize, isize> {
    let mut i = 0usize;
    if pal_size == 0 {
        while i < total {
            let pix = data.get(pos..pos + cp).ok_or(ERR_SHORT)?;
            pos += cp;
            let run = run_length(data, &mut pos).ok_or(ERR_SHORT)?;
            if run > total - i {
                return Err(ERR_OUTSIDE);
            }
            for chunk in out[i * cp..(i + run) * cp].chunks_exact_mut(cp) {
                chunk.copy_from_slice(pix);
            }
            i += run;
        }
    } else {
        while i < total {
            let mut index = *data.get(pos).ok_or(ERR_SHORT)? as usize;
            pos += 1;
            let mut run = 1usize;
            if index & 128 != 0 {
                run = run_length(data, &mut pos).ok_or(ERR_SHORT)?;
                index &= 127;
            }
            if index >= pal_size {
                return Err(ERR_INDEX);
            }
            if run > total - i {
                return Err(ERR_OUTSIDE);
            }
            out[i..i + run].fill(index as u8);
            i += run;
        }
    }
    Ok(pos)
}

fn fill(buf: &mut [u8], stride_px: usize, bp: usize, x: usize, y: usize,
        w: usize, h: usize, pix: &[u8]) {
    for row in y..y + h {
        let start = (row * stride_px + x) * bp;
        for chunk in buf[start..start + w * bp].chunks_exact_mut(bp) {
            chunk.copy_from_slice(pix);
        }
    }
}

/// The subrectangles of one Hextile tile (HextileDecoder.cxx).
///
/// `tile` is `tw * th` wire pixels of `bp` bytes, already filled with the
/// background.  `data` holds `n` subrects: [pixel if coloured] xy wh.
/// `fg` (bp bytes) is the foreground in and, since coloured subrects set
/// it for later tiles, the last colour out.
#[no_mangle]
pub unsafe extern "C" fn hextile_subrects(
    tile: *mut u8,
    tw: usize,
    th: usize,
    bp: usize,
    data: *const u8,
    n: usize,
    coloured: i32,
    fg: *mut u8,
) -> isize {
    if tile.is_null() || fg.is_null() || (n > 0 && data.is_null())
        || bp == 0 || bp > 4 || tw == 0 || th == 0 || tw > 16 || th > 16
    {
        return ERR_ARGS;
    }
    let each = if coloured != 0 { bp + 2 } else { 2 };
    let tile = slice::from_raw_parts_mut(tile, tw * th * bp);
    let fg = slice::from_raw_parts_mut(fg, bp);
    let data = if n > 0 { slice::from_raw_parts(data, n * each) } else { &[] };
    for sub in data.chunks_exact(each) {
        let mut o = 0;
        if coloured != 0 {
            fg.copy_from_slice(&sub[..bp]);
            o = bp;
        }
        let (xy, wh) = (sub[o] as usize, sub[o + 1] as usize);
        let (sx, sy) = (xy >> 4, xy & 15);
        let (sw, sh) = ((wh >> 4) + 1, (wh & 15) + 1);
        // Upstream only rejects subrects beyond 16x16; parts outside a
        // partial edge tile are clipped
        if sx + sw > 16 || sy + sh > 16 {
            return ERR_OUTSIDE;
        }
        if sx >= tw || sy >= th {
            continue;
        }
        let (sw, sh) = (sw.min(tw - sx), sh.min(th - sy));
        fill(tile, tw, bp, sx, sy, sw, sh, fg);
    }
    0
}

/// RRE / CoRRE subrectangles (RREDecoder.cxx) into `buf`, `w * h` wire
/// pixels of `bp` bytes already filled with the background.
#[no_mangle]
pub unsafe extern "C" fn rre_subrects(
    buf: *mut u8,
    w: usize,
    h: usize,
    bp: usize,
    data: *const u8,
    count: usize,
    compact: i32,
) -> isize {
    if buf.is_null() || (count > 0 && data.is_null()) || bp == 0 || bp > 4 {
        return ERR_ARGS;
    }
    let size = match w.checked_mul(h).and_then(|p| p.checked_mul(bp)) {
        Some(n) => n,
        None => return ERR_ARGS,
    };
    let each = bp + if compact != 0 { 4 } else { 8 };
    let buf = slice::from_raw_parts_mut(buf, size);
    let data = if count > 0 { slice::from_raw_parts(data, count * each) } else { &[] };
    let be16 = |b: &[u8]| ((b[0] as usize) << 8) | b[1] as usize;
    for sub in data.chunks_exact(each) {
        let pix = &sub[..bp];
        let r = &sub[bp..];
        let (sx, sy, sw, sh) = if compact != 0 {
            (r[0] as usize, r[1] as usize, r[2] as usize, r[3] as usize)
        } else {
            (be16(&r[0..]), be16(&r[2..]), be16(&r[4..]), be16(&r[6..]))
        };
        if sx + sw > w || sy + sh > h {
            return ERR_OUTSIDE;
        }
        fill(buf, w, bp, sx, sy, sw, sh, pix);
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gradient_first_row_is_cumulative() {
        let inp = [10u8, 20, 30, 1, 1, 1];
        let mut out = [0u8; 6];
        unsafe { tight_gradient(inp.as_ptr(), out.as_mut_ptr(), 2, 1) };
        assert_eq!(out, [10, 20, 30, 11, 21, 31]);
    }

    #[test]
    fn zrle_palette_run_outside_tile() {
        let data = [0x81u8, 5];            // index 1, run 6
        let mut out = [0u8; 4];
        let r = unsafe { zrle_rle(data.as_ptr(), 2, 0, 4, 3, 2, out.as_mut_ptr()) };
        assert_eq!(r, ERR_OUTSIDE);
    }
}
