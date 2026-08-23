package httpx

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

const (
	// DownloadPrefix is the HTTP path get_attachment's signed links live under.
	DownloadPrefix = "/attachments/"
	// DownloadTTL is how long a minted link remains valid.
	DownloadTTL = 15 * time.Minute
)

var (
	errBadToken = errors.New("invalid download token")
	errExpired  = errors.New("download token expired")
	errBadName  = errors.New("invalid filename")
	errNotFound = errors.New("attachment not found")
)

// SignDownload mints a token bound to filename and an expiry DownloadTTL
// after now. The token is path-safe: digits, a dot, and base64url.
func SignDownload(secret, filename string, now time.Time) (token string, expires time.Time) {
	expires = now.Add(DownloadTTL).UTC().Truncate(time.Second)
	exp := strconv.FormatInt(expires.Unix(), 10)
	return exp + "." + mac64(secret, exp, filename), expires
}

// VerifyDownload reports whether token is a valid, unexpired signature for
// filename. Every failure mode is distinct for tests; the HTTP handler
// collapses them to 404 so scanners learn nothing.
func VerifyDownload(secret, token, filename string, now time.Time) error {
	expStr, mac, ok := strings.Cut(token, ".")
	if !ok || expStr == "" || mac == "" {
		return errBadToken
	}
	expUnix, err := strconv.ParseInt(expStr, 10, 64)
	if err != nil {
		return errBadToken
	}
	if !hmac.Equal([]byte(mac), []byte(mac64(secret, expStr, filename))) {
		return errBadToken
	}
	if !now.Before(time.Unix(expUnix, 0).Add(time.Second)) {
		return errExpired
	}
	return nil
}

// DownloadURL builds the absolute URL an agent can fetch with curl.
func DownloadURL(baseURL, token, filename string) string {
	base := strings.TrimRight(strings.TrimSpace(baseURL), "/")
	return base + DownloadPrefix + token + "/" + url.PathEscape(filename)
}

// AttachmentHandler serves files previously written by get_attachment.
//
// Auth is the signed token in the path, not the bearer header: a remote
// agent has the MCP bearer, but curl on the agent's machine does not
// automatically attach it. The token is HMAC'd with the same secret and
// dies in DownloadTTL, so a leaked URL is a 15-minute window on one file,
// not a mailbox.
func AttachmentHandler(secret, dir string, logger *slog.Logger) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			w.Header().Set("Allow", "GET, HEAD")
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}

		path, err := resolveDownload(secret, dir, r.URL.Path, time.Now())
		if err != nil {
			logger.Debug("rejected attachment download",
				"remote", clientIP(r), "err", err.Error())
			w.WriteHeader(http.StatusNotFound)
			return
		}

		filename := filepath.Base(path)
		w.Header().Set("Content-Disposition", `attachment; filename="`+filename+`"`)
		http.ServeFile(w, r, path)
	})
}

func resolveDownload(secret, dir, requestPath string, now time.Time) (string, error) {
	rest, ok := strings.CutPrefix(requestPath, DownloadPrefix)
	if !ok {
		return "", errNotFound
	}
	token, rawName, ok := strings.Cut(rest, "/")
	if !ok || token == "" || rawName == "" || strings.Contains(rawName, "/") {
		return "", errNotFound
	}
	name, err := url.PathUnescape(rawName)
	if err != nil {
		return "", errBadName
	}
	// filepath.Base would turn "../secret" into "secret" and serve it if
	// the token happened to match. Reject anything that is not already a basename.
	if name != filepath.Base(name) || name == "" || name == "." || name == ".." {
		return "", errBadName
	}
	if err := VerifyDownload(secret, token, name, now); err != nil {
		return "", err
	}

	absDir, err := filepath.Abs(dir)
	if err != nil {
		return "", errNotFound
	}
	target := filepath.Join(absDir, name)
	if !underDir(absDir, target) {
		return "", errBadName
	}
	info, err := os.Stat(target)
	if err != nil || info.IsDir() {
		return "", errNotFound
	}
	return target, nil
}

func mac64(secret, exp, filename string) string {
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = fmt.Fprintf(mac, "%s\n%s", exp, filename)
	return base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

func underDir(dir, path string) bool {
	rel, err := filepath.Rel(dir, path)
	if err != nil {
		return false
	}
	return rel != ".." && !strings.HasPrefix(rel, ".."+string(os.PathSeparator))
}
