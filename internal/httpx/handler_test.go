package httpx

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestHandlerSplitsAuth(t *testing.T) {
	dir := t.TempDir()
	name := "photo.jpg"
	if err := os.WriteFile(filepath.Join(dir, name), []byte("jpeg"), 0o600); err != nil {
		t.Fatal(err)
	}
	token, _ := SignDownload(testSecret, name, time.Now())

	mcpHit := false
	mcp := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		mcpHit = true
		w.WriteHeader(http.StatusOK)
	})
	handler := Handler(testSecret, discardLogger(), false, 0, 0, mcp, AttachmentHandler(testSecret, dir, discardLogger()))

	t.Run("mcp without bearer is 401", func(t *testing.T) {
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/mcp", nil))
		if rec.Code != http.StatusUnauthorized {
			t.Errorf("status = %d, want 401", rec.Code)
		}
		if mcpHit {
			t.Error("mcp handler ran without a bearer token")
		}
	})

	t.Run("mcp with bearer reaches the inner handler", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodPost, "/mcp", nil)
		req.Header.Set("Authorization", "Bearer "+testSecret)
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Errorf("status = %d, want 200", rec.Code)
		}
		if !mcpHit {
			t.Error("mcp handler was not reached")
		}
	})

	t.Run("attachment without bearer is 200", func(t *testing.T) {
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, DownloadPrefix+token+"/"+name, nil))
		if rec.Code != http.StatusOK {
			t.Errorf("status = %d, want 200", rec.Code)
		}
		if rec.Body.String() != "jpeg" {
			t.Errorf("body = %q", rec.Body.String())
		}
	})

	t.Run("unknown path is empty 404", func(t *testing.T) {
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/admin", nil))
		if rec.Code != http.StatusNotFound || rec.Body.Len() != 0 {
			t.Errorf("status = %d body %q", rec.Code, rec.Body.String())
		}
	})
}
