"""Open your chosen websites in the default browser on the server computer."""
import argparse
from urllib.parse import urlsplit
import webbrowser


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="+", help="One or more complete http:// or https:// URLs")
    args = parser.parse_args()
    for url in args.urls:
        try:
            parsed = urlsplit(url)
            valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password
        except ValueError:
            valid = False
        if not valid or any(character.isspace() or ord(character) < 32 for character in url):
            parser.error(f"Use a complete HTTP(S) URL without credentials or whitespace: {url!r}")
    failed = False
    for url in args.urls:
        if webbrowser.open_new_tab(url):
            print(f"Requested browser tab: {url}")
        else:
            print(f"Could not open browser: {url}")
            failed = True
    if failed:
        parser.exit(1, "Run the server in your signed-in desktop session with a default browser configured.\n")


if __name__ == "__main__":
    main()
