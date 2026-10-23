"""Extract public company copy without models, credentials, redirects or private IPs."""
import asyncio
import http.client
import ipaddress
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

AGENT = 'OpenGrokbotProspecting'
MAX_BYTES = 524288


class ProfileError(ValueError):
    pass


def target(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ProfileError('Enter a public HTTPS company website.')
    parsed = urlsplit(value.strip())
    try:
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError()
        host = parsed.hostname.encode('idna').decode('ascii')
        if '.' not in host or host.endswith(('.local', '.internal', '.localhost')):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise ProfileError('Only public HTTPS hostnames on port 443 are supported.') from None
    return host, urlunsplit(('https', host, parsed.path or '/', parsed.query, ''))


def public_addresses(host):
    addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ProfileError('This website does not resolve exclusively to public addresses.')
    return addresses


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=3, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Connect to the validated address directly. TLS and Host still verify
        # the original hostname; a second DNS lookup cannot rebind to a LAN IP.
        raw = socket.create_connection((self.address, 443), timeout=3)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def read_page(host, address, path, limit):
    connection = PinnedHTTPS(host, address)
    deadline = time.monotonic() + 8
    try:
        connection.request('GET', path, headers={
            'User-Agent': AGENT + '/0.1 (+https://github.com/HIveAppDeveloper/open-grokbot)',
            'Accept': 'text/html,text/plain;q=0.8', 'Accept-Encoding': 'identity',
        })
        response = connection.getresponse()
        if response.status != 200:
            return response.status, '', b''
        if response.getheader('Content-Encoding', 'identity').lower() not in ('', 'identity'):
            raise ProfileError('This website requires an unsupported response encoding.')
        length = response.getheader('Content-Length')
        if length and (not length.isdigit() or int(length) > limit):
            raise ProfileError('The public page is too large to inspect safely.')
        data = bytearray()
        while True:
            if time.monotonic() > deadline:
                raise ProfileError('The public website took too long to respond.')
            chunk = response.read1(min(16384, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limit:
                raise ProfileError('The public page is too large to inspect safely.')
        return response.status, response.getheader('Content-Type', ''), bytes(data)
    finally:
        connection.close()


class CompanyCopy(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ''
        self.description = ''
        self.text = []
        self.in_title = False
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in ('script', 'style', 'noscript'):
            self.hidden += 1
        if tag == 'title':
            self.in_title = True
        if tag == 'meta' and (values.get('name', '').lower() == 'description' or values.get('property', '').lower() == 'og:description') and not self.description:
            self.description = values.get('content', '')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self.hidden = max(0, self.hidden - 1)
        if tag == 'title':
            self.in_title = False

    def handle_data(self, value):
        if self.in_title:
            self.title += value
        if not self.hidden and sum(map(len, self.text)) < 12000:
            self.text.append(value.strip())


def parse_copy(html, url):
    parser = CompanyCopy()
    parser.feed(html)
    clean = lambda value: re.sub(r'\s+', ' ', value).strip()
    title = clean(parser.title)[:300]
    description = clean(parser.description or ' '.join(parser.text))[:1500]
    if not description:
        raise ProfileError('No readable company description was available. Enter the offer manually.')
    # A phrase from website copy is a suggestion, never a model-generated claim
    # about buyers. The UI requires editable confirmation before discovery.
    match = re.search(r'\bfor\s+([A-Za-z][A-Za-z ,&/-]{3,100})(?:[.!?;]|$)', description)
    market = match.group(1).strip()[:100] if match else ''
    return {'url': url, 'title': title, 'description': description,
            'product': description, 'market': market,
            'retrieved_at': datetime.now(timezone.utc).isoformat(),
            'assumptions': ['Offer summary is extracted website copy; review it before use.',
                            'Suggested customers are an unverified phrase from the website. Confirm or replace the target market.'],
            'method': 'public_page_extract', 'requires_confirmation': True}


def _profile(value):
    host, url = target(value)
    address = public_addresses(host)[0]
    status, _, robots = read_page(host, address, '/robots.txt', 65536)
    if status == 200:
        rules = RobotFileParser()
        rules.parse(robots.decode('utf-8', errors='replace').splitlines())
        if not rules.can_fetch(AGENT, url):
            raise ProfileError('This website disallows automated access. Enter the offer manually.')
    elif status not in (404, 410):
        raise ProfileError('Website access rules could not be verified. Enter the offer manually.')
    parsed = urlsplit(url)
    status, content_type, body = read_page(host, address, parsed.path + ('?' + parsed.query if parsed.query else ''), MAX_BYTES)
    if status != 200:
        raise ProfileError('The public page could not be read. Redirects and restricted pages are not followed; use the final public URL.')
    if not any(kind in content_type.lower() for kind in ('text/html', 'application/xhtml+xml', 'text/plain')):
        raise ProfileError('The URL must return a public text or HTML company page.')
    return parse_copy(body.decode('utf-8', errors='replace'), url)


async def profile(value):
    try:
        return await asyncio.wait_for(asyncio.to_thread(_profile, value), timeout=18)
    except ProfileError:
        raise
    except (TimeoutError, OSError, http.client.HTTPException, UnicodeError, ValueError):
        raise ProfileError('The public company page is unavailable. Check the URL or enter the offer manually.') from None
