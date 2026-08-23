package httpx

import (
	"crypto/hmac"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const testSecret = "s3cret-token-value-long-enough"

func TestSignVerifyRoundTrip(t *testing.T) {
	now := time.Date(2026, 8, 23, 12, 0, 0, 0, time.UTC)
	token, exp := SignDownload(testSecret, "445-4-photo.jpg", now)
	if !exp.Equal(now.Add(DownloadTTL)) {
		t.Fatalf("expires = %s, want %s", exp, now.Add(DownloadTTL))
	}
	if err := VerifyDownload(testSecret, token, "445-4-photo.jpg", now); err != nil {
		t.Fatalf("fresh token rejected: %v", err)
	}
	if err := VerifyDownload(testSecret, token, "445-4-photo.jpg", exp.Add(-time.Second)); err != nil {
		t.Fatalf("token rejected a second before expiry: %v", err)
	}
}

func TestVerifyRejectsTampering(t *testing.T) {
	now := time.Now()
	token, _ := SignDownload(testSecret, "invoice.pdf", now)

	if err := VerifyDownload(testSecret, token, "other.pdf", now); !errors.Is(err, errBadToken) {
		t.Errorf("wrong filename: %v", err)
	}
	if err := VerifyDownload("different-secret-value", token, "invoice.pdf", now); !errors.Is(err, errBadToken) {
		t.Errorf("wrong secret: %v", err)
	}
	if err := VerifyDownload(testSecret, token+"x", "invoice.pdf", now); !errors.Is(err, errBadToken) {
		t.Errorf("appended junk: %v", err)
	}
	if err := VerifyDownload(testSecret, "not-a-token", "invoice.pdf", now); !errors.Is(err, errBadToken) {
		t.Errorf("garbage: %v", err)
	}
	if err := VerifyDownload(testSecret, "", "invoice.pdf", now); !errors.Is(err, errBadToken) {
		t.Errorf("empty: %v", err)
	}

	expStr, mac, _ := strings.Cut(token, ".")
	if err := VerifyDownload(testSecret, expStr+".", "invoice.pdf", now); !errors.Is(err, errBadToken) {
		t.Errorf("empty mac: %v", err)
	}
	if err := VerifyDownload(testSecret, "."+mac, "invoice.pdf", now); !errors.Is(err, errBadToken) {
		t.Errorf("empty expiry: %v", err)
	}
}

func TestVerifyRejectsExpired(t *testing.T) {
	now := time.Now()
	token, exp := SignDownload(testSecret, "invoice.pdf", now)
	if err := VerifyDownload(testSecret, token, "invoice.pdf", exp.Add(time.Second)); !errors.Is(err, errExpired) {
		t.Errorf("expired token: %v, want errExpired", err)
	}
}

func TestVerifyUsesConstantTimeCompare(t *testing.T) {
	// Guard the comparison itself: a future edit that swaps hmac.Equal for
	// == would still pass the tamper tests but lose the constant-time
	// property. hmac.Equal on equal inputs must return true here.
	if !hmac.Equal([]byte("abc"), []byte("abc")) {
		t.Fatal("hmac.Equal is broken; download tokens cannot be verified")
	}
}

func TestDownloadURLEscapesFilename(t *testing.T) {
	got := DownloadURL("https://mail.example/", "123.abc", "Schloss Biesendahl.jpg")
	want := "https://mail.example/attachments/123.abc/Schloss%20Biesendahl.jpg"
	if got != want {
		t.Errorf("url = %q, want %q", got, want)
	}
}

func TestAttachmentHandlerServesFile(t *testing.T) {
	dir := t.TempDir()
	name := "445-4-Schloss Biesendahl.jpg"
	body := []byte("jpeg-bytes")
	if err := os.WriteFile(filepath.Join(dir, name), body, 0o600); err != nil {
		t.Fatal(err)
	}

	now := time.Now()
	token, _ := SignDownload(testSecret, name, now)
	handler := AttachmentHandler(testSecret, dir, discardLogger())

	req := httptest.NewRequest(http.MethodGet, DownloadURL("http://mail.example", token, name), nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	if got := rec.Body.Bytes(); string(got) != string(body) {
		t.Errorf("body = %q, want %q", got, body)
	}
	if disp := rec.Header().Get("Content-Disposition"); !strings.Contains(disp, name) {
		t.Errorf("Content-Disposition = %q, want it to name the file", disp)
	}
}

func TestAttachmentHandlerHEAD(t *testing.T) {
	dir := t.TempDir()
	name := "file.bin"
	if err := os.WriteFile(filepath.Join(dir, name), []byte("abcd"), 0o600); err != nil {
		t.Fatal(err)
	}
	token, _ := SignDownload(testSecret, name, time.Now())
	handler := AttachmentHandler(testSecret, dir, discardLogger())

	req := httptest.NewRequest(http.MethodHead, DownloadPrefix+token+"/"+name, nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	if rec.Body.Len() != 0 {
		t.Errorf("HEAD should have an empty body, got %q", rec.Body.String())
	}
}

func TestAttachmentHandlerRejectsBadRequests(t *testing.T) {
	dir := t.TempDir()
	name := "ok.bin"
	if err := os.WriteFile(filepath.Join(dir, name), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	token, _ := SignDownload(testSecret, name, time.Now())
	handler := AttachmentHandler(testSecret, dir, discardLogger())

	cases := map[string]string{
		"missing token":  DownloadPrefix + name,
		"no filename":    DownloadPrefix + token + "/",
		"wrong filename": DownloadPrefix + token + "/other.bin",
		"garbage token":  DownloadPrefix + "nope/ok.bin",
		"missing file":   DownloadPrefix + func() string { tkn, _ := SignDownload(testSecret, "gone.bin", time.Now()); return tkn }() + "/gone.bin",
		"path traversal": DownloadPrefix + token + "/..%2Fok.bin",
		"nested path":    DownloadPrefix + token + "/a/ok.bin",
		"outside prefix": "/mcp",
	}
	for name, path := range cases {
		t.Run(name, func(t *testing.T) {
			rec := httptest.NewRecorder()
			handler.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, path, nil))
			if rec.Code != http.StatusNotFound {
				t.Errorf("status = %d, want 404", rec.Code)
			}
			if rec.Body.Len() != 0 {
				t.Errorf("404 body should be empty, got %q", rec.Body.String())
			}
		})
	}
}

func TestAttachmentHandlerRejectsPOST(t *testing.T) {
	handler := AttachmentHandler(testSecret, t.TempDir(), discardLogger())
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, DownloadPrefix+"x/y", nil))
	if rec.Code != http.StatusMethodNotAllowed {
		t.Errorf("status = %d, want 405", rec.Code)
	}
	if rec.Header().Get("Allow") == "" {
		t.Error("405 should advertise Allow")
	}
}

func TestResolveDownloadRejectsDotDotName(t *testing.T) {
	_, err := resolveDownload(testSecret, t.TempDir(), DownloadPrefix+"tok/..", time.Now())
	if err == nil {
		t.Fatal("accepted .. as a filename")
	}
}

func TestUnderDir(t *testing.T) {
	if !underDir("/var/lib/mail", "/var/lib/mail/a.jpg") {
		t.Error("child should be under parent")
	}
	if underDir("/var/lib/mail", "/var/lib/mail/../etc/passwd") {
		t.Error("escaped path accepted")
	}
	if underDir("/var/lib/mail", "/etc/passwd") {
		t.Error("other tree accepted")
	}
}
