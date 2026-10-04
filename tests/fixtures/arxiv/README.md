# arXiv API fixtures

Recorded responses of the public arXiv API, used by offline parser tests.
They contain descriptive metadata only, which arXiv makes available under
CC0 1.0 (https://info.arxiv.org/help/api/tou.html); no paper content.

| File | Request | Recorded (UTC) | Status | SHA-256 |
|---|---|---|---|---|
| `1706.03762.atom.xml` | `GET https://export.arxiv.org/api/query?id_list=1706.03762` | 2026-10-04T17:10:37Z | 200, `application/atom+xml; charset=utf-8`, 2965 bytes | `5bba183d41a014e2768ebf24c5e161a87ee2cff6d7ffa078fdde20a97ca64a0b` |

Stored byte for byte as received. Notes for the parser: the entry `<id>`
uses `http://` while its links use `https://`; this entry has no
`<arxiv:doi>`; the feed-level `<id>` and `<updated>` differ on every request.
