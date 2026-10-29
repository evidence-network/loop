# d3 7.9.0, vendored

`d3.min.js` and `LICENSE` (ISC) are copied unchanged from the npm package
`d3@7.9.0`, file `dist/d3.min.js`, and the package's `LICENSE`.

- npm `dist.integrity` of the tarball:
  `sha512-e1U46jVP+w7Iut8Jt8ri1YsPOvFpg46k+K8TpCb0P+zjCkjkPnV7WzfDJzMHy1LnA+wj5pLT1wjO901gLXeEhA==`
- sha256 of `d3.min.js`: `f2094bbf6141b359722c4fe454eb6c4b0f0e42cc10cc7af921fc158fceb86539`

The watch page (`site/watch.py`) copies both to `_site/watch/vendor/d3-7.9.0/`
and loads the script from that relative path. The page uses d3's force layout
(`d3.forceSimulation` and its forces) and `d3.zoom`. It never calls d3's
fetch helpers; its only read is its own `data.json`.

`tests/test_watch.py` checks the sha256 above and that `LICENSE` is here.
To upgrade, replace both files from the new npm tarball, check its
integrity against npm's, and update this README, the directory name,
`D3` in `site/watch.py`, and the pin in the test.
