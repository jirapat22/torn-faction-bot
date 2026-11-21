# Saved Torn wiki pages

The Torn wiki blocks bots, so @ApexPunk answers game questions from copies
saved here by hand (`features/torn_wiki.py` reads every `.md` file in this
folder; this README is skipped).

## Format, one file per wiki page

```
# Organized Crime
Source: https://wiki.torn.com/wiki/Organized_Crime
Saved: 22/11/2026

## Roles
...text copied from the page...

## Success chance
...
```

- `# Title` on the first line: the page name (it is matched against questions).
- `Source:` line: the page URL, shown to the AI as where the copy came from.
- `## Heading` sections: the bot quotes only the sections that match a
  question, so keep each topic under its own heading.
- Copies do not update themselves. Re-paste a page when Torn changes it, and
  update the `Saved:` date.
- No prices or Xanax details: those lines are dropped from chat anyway (Pat's rule).
