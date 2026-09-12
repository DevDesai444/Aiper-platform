"use client";

/**
 * Last-resort boundary, used when the root layout itself fails. It must render
 * its own <html> and cannot rely on the app's styles, so it stays plain.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "grid",
          placeItems: "center",
          fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
          background: "#fbfbfa",
          color: "#18181b",
        }}
      >
        <div style={{ maxWidth: 560, padding: 24 }}>
          <h1 style={{ fontSize: 17, margin: 0 }}>The application could not start</h1>
          <pre
            style={{
              marginTop: 16,
              padding: 12,
              border: "1px solid #e4e4e7",
              borderRadius: 8,
              fontSize: 11,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              maxHeight: 320,
              overflow: "auto",
            }}
          >
            {error.message || String(error)}
            {error.digest ? `\n\ndigest: ${error.digest}` : ""}
            {error.stack ? `\n\n${error.stack}` : ""}
          </pre>
          <button
            type="button"
            onClick={reset}
            style={{
              marginTop: 16,
              padding: "8px 14px",
              borderRadius: 6,
              border: "1px solid #e4e4e7",
              background: "#18181b",
              color: "#fff",
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
