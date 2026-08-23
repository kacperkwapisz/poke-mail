package httpx

import (
	"log/slog"
	"net/http"
)

const mcpPath = "/mcp"

// Handler is the HTTP surface: bearer-authenticated /mcp, and
// signed-URL /attachments/ for files get_attachment has already written.
func Handler(apiKey string, logger *slog.Logger, trustProxy bool, getRPM, postRPM int, mcp, attachments http.Handler) http.Handler {
	limiter := NewRateLimiter(getRPM, postRPM, trustProxy)

	wrap := func(inner http.Handler, bearer bool) http.Handler {
		if bearer {
			inner = RequireBearer(apiKey, logger, inner)
		}
		inner = limiter.Middleware(inner)
		inner = SecurityHeaders(inner)
		inner = LogRequests(logger, inner)
		return inner
	}

	mux := http.NewServeMux()
	mcpWrapped := wrap(mcp, true)
	mux.Handle(mcpPath, mcpWrapped)
	mux.Handle(mcpPath+"/", mcpWrapped)
	mux.Handle(DownloadPrefix, wrap(attachments, false))
	return OnlyPath(mux, mcpPath, DownloadPrefix)
}
