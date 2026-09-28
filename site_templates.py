"""Explicit static asset paths for a public site hosted under any base path."""
import re


def public_html(text):
    # Only assets change here. Navigation and public/local capabilities remain
    # explicit in their templates; API routes are never converted to data files.
    return re.sub(r'(src|href)="/([^"?]+\.(?:js|css))(\?[^"\s]*)?"',
                  lambda m: f'{m[1]}="./{m[2]}{m[3] or ""}"', text)
