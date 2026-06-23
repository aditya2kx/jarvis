# Dogfood evidence

This directory holds end-to-end lifecycle conformance runs produced by
`scripts/dogfood_lifecycle.py`. Each `lifecycle-run-<date>.md` is an annotated
transcript proving a dummy requirement walked all 12 substeps of the Jarvis
lifecycle (scripts/lifecycle.py), with operator-reserved gates enforced and a
real operator merge.

Regenerate with:

```bash
python3 scripts/dogfood_lifecycle.py run
# approve the dummy PR, then:
python3 scripts/dogfood_lifecycle.py resume
```
