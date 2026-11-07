from http.server import BaseHTTPRequestHandler, HTTPServer


HOST = "localhost"
PORT = 8000 # change by computer # -----------------------------------------


class WebsiteHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self.send_error(404)
            return

        page = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Souper Grants</title>
    <style>
      <!-- One stage in a 1600×900 design space: 1cqw = 16 design px. -->
      :root {
        color-scheme: light;
        --cream: #f5f1e8;
        --paper: #fffdf8;
        --ink: #173c36;
        --muted: #5f6f65;
        --faint: #9aa79e;
        --line: #d6d9ca;
        --lime: #cfe4a6;
        --coral: #c95b41;
        --coral-soft: #f7dfd3;
        --teal: #2e7d70;
        --stone: #b7c4bd;
        --sans: "Avenir Next", "Segoe UI", Arial, sans-serif;
        --serif: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
        --mono: ui-monospace, SFMono-Regular, Consolas, monospace;
      }

      # body {
      #   font-family: Arial, sans-serif;
      #   margin: 20px;
      # }
      # h1 {
      #   color: #333;
      #   text-align: center;
      # }
      div {
        <!-- Taken from https://www.w3schools.com/css/css_boxmodel.asp -->
        width: 300px;
        border: 15px solid green;
        padding: 50px;
        margin: 20px;
      }
    </style>
  </head>
  <body>
    <h1>Welcome to Souper Grants!</h1>
    <h2>The only place you'll need to explore federal grants</h2>
    <p>With 1,662 federal grants here alone, and thousands more out there, we've got you covered.</p>
    <p>Use the filters to categorize what you're looking for.</p>
    <div class="my-box"> 
      <!-- Taken from https://www.w3schools.com/css/css_boxmodel.asp -->
      <h3>Grant Categories</h3>
      <p>Transportation, Infrastructure, Education, and many more</p>
    </div>

  </body>
</html>
"""
        content = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    server = HTTPServer((HOST, PORT), WebsiteHandler)
    print(f"Souper Grants is running at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()