# xtream-probe

I made this for myself because I kept getting lists of Xtream Codes servers
and didn't want to check them one by one. 

It takes one username and password and checks them against each server in a
list by hitting `player_api.php`. It tells me which ones are up and working,
which ones reject the login, and which ones are dead. The working ones get
saved to a file so I can use them right away. No extra dependencies, just
Python.

## Requirements

- Python 3.9 or newer
- No runtime dependencies (standard library only)
- `pytest` only if you want to run the test suite

Works on macOS, Linux, and Windows.

## Quick start

```bash
python3 xstream_probe.py --username YOUR_USER --password YOUR_PASS
```

By default the script:

- reads server URLs from `data/urls.txt`
- tests every URL against `{server}/player_api.php`
- prints one line per server
- writes the working URLs to `working_urls.csv`

## Usage

```text
python3 xstream_probe.py [options]

options:
  --username USERNAME    Xtream Codes username
  --password PASSWORD    Xtream Codes password
  --urls PATH            URL list file, .txt or .csv (default: data/urls.txt)
  --output PATH          output file for working URLs (default: working_urls.csv)
  --timeout SECONDS      per-server timeout (default: 10)
  --no-color             disable colored terminal output
  --version              show the version and exit
  --help                 show the full help text
```

### Passing credentials safely

Passing a password on the command line can expose it in your shell history and
process list. Two safer options are available:

```bash
# Read credentials from environment variables
export XSTREAM_USERNAME="your_user"
export XSTREAM_PASSWORD="your_pass"
python3 xstream_probe.py
```

```bash
# Omit --password and the script prompts without echoing it
python3 xstream_probe.py --username your_user
```

The password is never printed or written to the output file.

### Examples

```bash
# Check the bundled list and save results
python3 xstream_probe.py --username user --password pass

# Check a custom list with a shorter timeout
python3 xstream_probe.py \
  --username user --password pass \
  --urls data/streams.csv \
  --timeout 5

# Write results somewhere else
python3 xstream_probe.py \
  --username user --password pass \
  --output reports/working.txt
```

## Input formats

### Plain text (`.txt`)

One base URL per line. Blank lines and lines starting with `#` are ignored.

```text
# mirrors
http://mirror-one.example.com:8080
https://mirror-two.example.net
```

### CSV (`.csv`)

The first column is treated as the base URL. A header row such as `URL` is
skipped, and quoted values are supported.

```csv
URL,notes
http://mirror-one.example.com:8080,primary
http://mirror-two.example.net,backup
```

URLs are trimmed, have surrounding quotes and trailing slashes removed, and are
deduplicated (host names are compared case-insensitively). Invalid or non-HTTP
URLs are reported on stderr and skipped.

## Output and statuses

Each server is printed as one of:

| Status | Meaning |
| --- | --- |
| `[OK]` | `auth` is `1` and the account is active. Shows the expiry date when available. |
| `[AUTH-FAIL]` | Credentials were rejected (`auth` is `0`), or the account status is not active. |
| `[DOWN]` | Connection, DNS, TLS, or timeout failure. A short reason is shown. |
| `[UNKNOWN]` | The response was not valid JSON or did not contain the expected fields. |

A summary is printed at the end:

```text
2/27 servers OK
Working URLs saved to: working_urls.csv (2 unique)
```

Only `[OK]` URLs are written to the output file, one per line, deduplicated.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | At least one server is OK |
| `1` | No server is OK |
| `2` | Invalid usage, missing input file, or empty URL list |
| `130` | Interrupted with Ctrl+C |

## Running the tests

The tests use mocked HTTP responses and do not contact any servers.

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest
```

Or, without installing anything globally:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

## Project layout

```text
xstream_probe/
├── xstream_probe.py          # the CLI (single file, stdlib only)
├── README.md
├── requirements-dev.txt      # pytest, for development only
├── .gitignore
├── data/
│   ├── urls.txt              # default server list
│   ├── streams.csv           # alternate list
│   └── stream_old.csv        # older list kept for reference
├── tests/
│   ├── conftest.py
│   └── test_xstream_probe.py
└── working_urls.csv          # generated output (not source)
```

## Sending this to a friend

The project is self-contained. Share the whole folder, or create an archive
without generated files and caches:

```bash
zip -r xstream_probe.zip . \
  -x ".venv/*" "*/__pycache__/*" "*.pyc" ".pytest_cache/*" "working_urls.csv"
```

Your friend only needs Python 3.9+ and their own credentials:

```bash
python3 xstream_probe.py --username THEIR_USER --password THEIR_PASS
```

## Notes and limitations

- Checks run sequentially to keep behavior simple and predictable.
- The script only calls `player_api.php`; it does not fetch channel lists or
  stream content.
- Expiry timestamps are rendered in the local timezone as `YYYY-MM-DD`.
- `data/` contains provider domains supplied with the original project. Update
  or replace them as needed.
- Use this only with accounts and services you are allowed to access. The
  author of this tool is not responsible for how it is used.
