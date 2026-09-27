package testsupport

import (
	"bytes"
	"encoding/binary"
	"hash/crc32"
	"image"
	"image/color"
	"image/jpeg"
	"image/png"
	"testing"
)

// JPEG encodes a real w x h JPEG. Fixtures are generated at run time so the
// repository carries no binary test files.
func JPEG(t testing.TB, w, h int) []byte {
	t.Helper()
	var buf bytes.Buffer
	if err := jpeg.Encode(&buf, pattern(w, h), &jpeg.Options{Quality: 90}); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

// PNG encodes a real w x h PNG.
func PNG(t testing.TB, w, h int) []byte {
	t.Helper()
	var buf bytes.Buffer
	if err := png.Encode(&buf, pattern(w, h)); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

// PNGClaiming returns a tiny, otherwise valid 1x1 PNG whose header claims w x h
// (with a correct chunk CRC): a small payload that declares an unreasonable
// decoded size.
func PNGClaiming(t testing.TB, w, h uint32) []byte {
	t.Helper()
	data := PNG(t, 1, 1)
	// Signature (8) + IHDR length (4) + "IHDR" (4) = offset 16 of width.
	binary.BigEndian.PutUint32(data[16:20], w)
	binary.BigEndian.PutUint32(data[20:24], h)
	binary.BigEndian.PutUint32(data[29:33], crc32.ChecksumIEEE(data[12:29]))
	return data
}

func pattern(w, h int) image.Image {
	img := image.NewRGBA(image.Rect(0, 0, w, h))
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			img.Set(x, y, color.RGBA{R: uint8(x * 7), G: uint8(y * 5), B: uint8((x + y) * 3), A: 255})
		}
	}
	return img
}
