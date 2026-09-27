package media_test

import (
	"bytes"
	"errors"
	"io"
	"strings"
	"testing"
	"time"

	"github.com/nawariso/toem-hia/services/api/internal/domain/media"
	"github.com/nawariso/toem-hia/services/api/internal/testsupport"
)

func opener(data []byte) func() (io.ReadCloser, error) {
	return func() (io.ReadCloser, error) { return io.NopCloser(bytes.NewReader(data)), nil }
}

func TestInspectAcceptsRealJPEGAndPNG(t *testing.T) {
	for name, tc := range map[string]struct {
		data []byte
		want string
	}{
		"jpeg": {testsupport.JPEG(t, 64, 48), media.ContentTypeJPEG},
		"png":  {testsupport.PNG(t, 40, 30), media.ContentTypePNG},
	} {
		t.Run(name, func(t *testing.T) {
			info, err := media.Inspect(opener(tc.data))
			if err != nil {
				t.Fatal(err)
			}
			if info.ContentType != tc.want || info.Width == 0 || info.Height == 0 {
				t.Fatalf("unexpected info %+v", info)
			}
		})
	}
}

func TestInspectRejectsNonImagesAndMalformedImages(t *testing.T) {
	jpegBytes := testsupport.JPEG(t, 64, 48)
	for name, data := range map[string][]byte{
		"empty":            {},
		"html":             []byte("<html><script>alert(1)</script></html>"),
		"gif":              []byte("GIF89a\x01\x00\x01\x00\x00\x00\x00;"),
		"jpeg magic only":  {0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 'J', 'F', 'I', 'F'},
		"truncated jpeg":   jpegBytes[:len(jpegBytes)/2],
		"png magic + junk": append([]byte{0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n'}, []byte("not a chunk")...),
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := media.Inspect(opener(data)); !errors.Is(err, media.ErrInvalidImage) {
				t.Fatalf("want ErrInvalidImage, got %v", err)
			}
		})
	}
}

func TestInspectRejectsUnreasonableDimensionsBeforeDecoding(t *testing.T) {
	for name, data := range map[string][]byte{
		"edge too long":   testsupport.PNG(t, media.MaxEdge+1, 1),
		"claimed 60k x 1": testsupport.PNGClaiming(t, 60000, 1),
		"claimed 8000x7000 (56 MP) in a tiny payload": testsupport.PNGClaiming(t, 8000, 7000),
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := media.Inspect(opener(data)); !errors.Is(err, media.ErrDimensions) {
				t.Fatalf("want ErrDimensions, got %v", err)
			}
		})
	}
}

func TestDeclaredTypeAllowsOnlyJPEGAndPNG(t *testing.T) {
	for header, want := range map[string]string{
		"image/jpeg": media.ContentTypeJPEG, "IMAGE/JPEG": media.ContentTypeJPEG,
		"image/png; charset=binary": media.ContentTypePNG,
	} {
		if got, err := media.DeclaredType(header); err != nil || got != want {
			t.Fatalf("%q: got %q %v", header, got, err)
		}
	}
	for _, header := range []string{"", "image/gif", "image/heic", "image/webp", "application/octet-stream", "text/html", "multipart/form-data; boundary=x", "image/jpeg;;"} {
		if _, err := media.DeclaredType(header); !errors.Is(err, media.ErrUnsupportedType) {
			t.Fatalf("%q must be unsupported, got %v", header, err)
		}
	}
}

func TestNewPhotoGeneratesServerSideStorageKey(t *testing.T) {
	m := media.NewPhoto("enc", media.Info{ContentType: media.ContentTypePNG, Width: 2, Height: 3}, 10, strings.Repeat("a", 64), time.Now())
	if !media.ValidStorageKey(m.StorageKey) || !strings.HasSuffix(m.StorageKey, ".png") || !strings.Contains(m.StorageKey, m.ID) {
		t.Fatalf("unexpected storage key %q", m.StorageKey)
	}
	other := media.NewPhoto("enc", media.Info{ContentType: media.ContentTypeJPEG, Width: 2, Height: 3}, 10, strings.Repeat("a", 64), time.Now())
	if other.StorageKey == m.StorageKey || !strings.HasSuffix(other.StorageKey, ".jpg") {
		t.Fatalf("keys must be unique per photo: %q %q", m.StorageKey, other.StorageKey)
	}
	if m.Kind != media.KindPhoto || m.Status != media.StatusReady {
		t.Fatalf("unexpected kind/status %+v", m)
	}
}

func TestValidStorageKeyRejectsPathsAndTraversal(t *testing.T) {
	for _, key := range []string{
		"", "photos/", "../photos/x.jpg", "photos/../../etc/passwd", "/etc/passwd", `photos\x.jpg`,
		"photos/0f8fad5b-d9cb-469f-a165-70867728950e.jpg/..", "photos/0F8FAD5B-D9CB-469F-A165-70867728950E.jpg",
		"photos/0f8fad5b-d9cb-469f-a165-70867728950e.gif", "tmp/0f8fad5b-d9cb-469f-a165-70867728950e.jpg",
		"C:/photos/0f8fad5b-d9cb-469f-a165-70867728950e.jpg",
	} {
		if media.ValidStorageKey(key) {
			t.Fatalf("%q must be rejected", key)
		}
	}
	if !media.ValidStorageKey("photos/0f8fad5b-d9cb-469f-a165-70867728950e.jpg") {
		t.Fatal("server-generated key shape must be accepted")
	}
}
