"""Start the Google Search Scraper API.

    python run.py            # http://localhost:8000
    PORT=9000 python run.py  # another port

Then:  curl "http://localhost:8000/search?query=best+laptop+2026"
"""
import bottle
from cheroot import wsgi

import config
import routes  # noqa: F401  (mounts the routes on bottle's default app)


def main():
    app = bottle.default_app()
    print(f"Google Search Scraper listening on http://localhost:{config.PORT}/")
    print(f"Try:  curl \"http://localhost:{config.PORT}/search?query=best+laptop+2026\"")
    if not config.CAPSOLVER_API_KEY:
        print("Tip:  set CAPSOLVER_API_KEY to solve Google's captcha automatically when it shows up "
              "(see config.py).")
    server = wsgi.Server(("0.0.0.0", config.PORT), app, server_name="google-search-scraper", numthreads=16)
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
