# Design System

## Map (Round 4 D72)
- Basemap: Esri World Imagery (satellite) + CartoDB dark-only-labels overlay.
  Rationale: CartoDB Dark Matter rendered rural Nepal as near-black emptiness;
  satellite imagery makes the Himalayan terrain read clearly under the data
  layers. Attribution carries Esri + Maxar + OSM.
- The map renders FULL container width (st_folium use_container_width=True).
  Never use st_folium's default width (500 px) - that was the "map not
  showing" bug.
- The legend is a Streamlit-rendered chip row (.fs-leg-item), NEVER a folium
  figure child (a figure-root child forces folium's double float layout).
: FloodSight

## 1. Visual Theme & Atmosphere

A premium utilitarian-minimalist operational dashboard for satellite flood
intelligence. The atmosphere is a well-lit cartography studio meeting a
modern aerospace mission-control readout: calm, precise, trustworthy. Density
sits at 6 (Daily App Balanced trending toward Cockpit Dense when the data
tables fill) — this is a working tool for rescue coordinators, not a
landing page. Variance is 4 (controlled asymmetry: the 16:9 map dominates
the left two-thirds, summary cards and the status panel hold the right
edge). Motion is 3 (Static Restrained): quiet fade-ins and staggered
reveals only; no spectacle — a disaster tool that feels "designed" in the
flashy sense loses trust instantly. Typography and whitespace carry the
hierarchy; the map carries the story.

## 2. Color Palette & Roles

- **Canvas Warm Bone** (#F7F6F3) — Primary background surface
- **Pure Surface** (#FFFFFF) — Card, sidebar and container fill
- **Charcoal Ink** (#18181B) — Primary text, Zinc-950 depth (never #000000)
- **Muted Steel** (#71717A) — Secondary text, metadata, captions, coordinate readouts
- **Whisper Border** (rgba(0,0,0,0.06)) — 1px structural dividers, card outlines
- **Signal Red** (#D62728) — Single accent: "likely cut off" settlements, primary alert CTA, upstream-point star
- Muted pastel badges only for status tags:
  - Pale Red #FDEBEC / text #9F2F2D — "likely affected"
  - Pale Amber #FBF3DB / text #956400 — "possibly affected"
  - Pale Green #EDF3EC / text #346538 — "connected"
  - Pale Blue #E1F3FE / text #1F6C9F — "not analysed"
- Map layer colours (from the shared LAYER_STYLE config — the legend is
  generated from this same table and cannot drift):
  - Flood water #1f77b4; Model flood #9467bd; Debris #8c564b;
    Damaged building #e377c2; Damaged road #bcbd22; Affected bridge #17becf;
    Flood path #2ca02c; Bbox outline #111111; Analysed area #aaaaaa

Max 1 saturated accent (Signal Red). No purple/neon. No gradients.

## 3. Typography Rules

- **Display/Headlines:** Satoshi (fallback: 'Helvetica Neue', sans-serif) —
  Track-tight (-0.02em), controlled scale, weight-driven hierarchy. H1 at
  2rem/700, section headers at 1.25rem/600. Hierarchy through weight and
  the Charcoal Ink vs Muted Steel contrast, not massive size.
- **Body:** Satoshi — relaxed leading (1.6), max 65ch, Charcoal Ink
  primary with Muted Steel for secondary. Minimum 1rem/16px.
- **Mono (all numbers, coordinates, dates, scene IDs, telemetry):**
  JetBrains Mono — 0.875rem, uppercase tracking 0.05em for labels; every
  measured figure (km², metres, counts, percentages) renders in mono so
  digits align in columns and read as data, not prose.
- **Banned:** Inter, Roboto, Open Sans. All serif fonts (dashboard rule).
  Font sizes below 14px.

## 4. Component Stylings

- **Buttons:** Flat Charcoal Ink fill (#111111) with white text, 4-6px
  radius, no shadow. Hover: #333333. Active: scale(0.98) tactile push.
  Exactly one primary CTA per view ("Run analysis (real data)"); secondary
  actions are ghost/outline.
- **Cards (summary metrics):** border 1px solid #EAEAEA, 8px radius, 24px
  internal padding, white fill. The four metric cards sit in a single row
  on desktop, 2×2 on tablet, single column on mobile. No drop shadows.
- **Inputs/Forms (bbox + date):** Label above (mono, uppercase, 0.75rem,
  Muted Steel), 8px radius, 1px #EAEAEA border, focus ring in Charcoal
  Ink. Error below in Pale Red tag style. No floating labels.
- **Map:** Full-width 16:9 (CSS aspect-ratio), OpenStreetMap base tiles,
  collapsible legend (top-right, generated from LAYER_STYLE), layers with
  45% fill opacity and 1.5px lines. Folium/Leaflet with attribution
  footer always visible.
- **Status panel (provenance):** collapsible expander, 1px top border,
  mono list of pair dates, track, coverage %, snapshot date, warnings as
  pastel badges. No cards-in-cards.
- **Loading:** skeleton shimmer matching the layout dimensions of the
  stage-progress rows; no circular spinners.
- **Empty/Error states:** composed honest messages ("No run selected yet.
  Use New analysis in the sidebar…"), never blank frames.

## 5. Layout Principles

- Wide layout (Streamlit `layout="wide"`), full-bleed map left-to-right,
  sidebar (380px) holds the input form and the run selector.
- CSS Grid for the metric row; no flexbox percentage math.
- Max-width containment 1400px for prose sections; the map spans
  container width.
- Vertical section gaps: clamp(2rem, 5vw, 4rem).
- No overlapping elements; every layer, card and panel occupies its own
  spatial zone. The analysed-area outline and bbox outline are drawn on
  the map, never as absolutely positioned DOM overlays.
- Mobile-first collapse below 768px: metric cards single column, map
  stacks below cards, sidebar collapses to a top drawer.

## 6. Motion & Interaction

- Staggered fade-in on load: translateY(12px) + opacity 0→1 over 600ms,
  cubic-bezier(0.16, 1, 0.3, 1), cascade delay 80ms per card. Via
  IntersectionObserver, never scroll listeners.
- Live stage progress: each stage row appears with a 150ms fade when its
  status changes; no progress bars, no fake animation — the statuses are
  read from the run folder.
- Hover: cards lift with an ultra-diffuse shadow (0 2px 8px
  rgba(0,0,0,0.04) over 200ms). Map layers highlight on legend hover.
- Animate transform and opacity only. No top/left/width/height animation.
- No perpetual micro-loops: this is an operational tool; idle animation
  would imply activity that is not happening.

## 7. Anti-Patterns (Banned)

- No emojis anywhere
- No Inter, Roboto, or Open Sans
- No serif fonts (dashboard rule)
- No pure black (#000000) — Charcoal Ink #18181B instead
- No neon/outer glow shadows, no gradients on large surfaces
- No oversaturated accents — Signal Red is the only saturated colour
- No custom mouse cursors
- No overlapping elements
- No 3-column equal card grids — the metric row is 4 asymmetric-weight
  cards where coverage carries a subordinate caption
- No generic placeholder names — real run IDs (e.g. "84.390_27.680_
  85.730_28.310_2026-08-26_fast") and real settlement names
- No fake round numbers — every figure renders from the results store
- No AI copywriting clichés ("Elevate your flood mapping", "Seamless
  satellite intelligence", "Next-gen disaster response")
- No filler UI text ("Scroll to explore", "Swipe down")
- No broken image links — all figures from local docs/figures/
- No fabricated data displays — if a stage has not run, the panel says so
