# Slides

```bash
pdflatex cover.tex      # -> cover.pdf   (320x180mm, renders to 1920x1080)
pdflatex trisolver.tex  # twice, for the title/close overlays
```

- `palette.tex` — shared colours. The category colours are the same values the
  terminal report uses, so a judge watching the demo sees one colour mean one
  thing.
- `theme.tex` — minimal dark beamer theme.
- `cover.tex` → `cover.png` — submission cover image.
- `trisolver.tex` → `trisolver.pdf` — 13-slide deck.

PNG export (no ImageMagick/poppler on this box):

```bash
uv run --no-project --with pymupdf python -c "
import pymupdf; d=pymupdf.open('cover.pdf'); p=d[0]; z=1920/p.rect.width
p.get_pixmap(matrix=pymupdf.Matrix(z,z)).save('cover.png')"
```

Built with a minimal TeX Live: beamer, tikz, xcolor, lmodern only. No
fontawesome, helvet metrics, microtype or `shadows.blur` — avoid them.
