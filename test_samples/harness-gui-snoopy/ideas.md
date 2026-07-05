# Harness GUI - Snoopy Style Design Philosophy

## Chosen Approach: "Peanuts Comic Panel Aesthetic"

### Design Movement
**Vintage Comic Illustration** — inspired by Charles M. Schulz's Peanuts comic strip aesthetic from the 1960s-70s, featuring hand-drawn line art, simple geometric forms, and a playful yet purposeful visual language.

### Core Principles
1. **Hand-Drawn Authenticity**: Imperfect lines, organic borders, and sketch-like elements convey approachability and warmth while maintaining technical clarity
2. **Minimalist Geometry**: Simple shapes (circles, rectangles, speech bubbles) combined with clean typography to organize complex information hierarchically
3. **Playful Precision**: Whimsical details (doodles, thought bubbles, comic-style borders) balance serious functionality—the app feels intelligent but never intimidating
4. **High Contrast Clarity**: Black ink on white/cream backgrounds with selective color accents (red, blue) for emphasis, ensuring readability and focus

### Color Philosophy
- **Primary**: Black (`#1a1a1a`) — authoritative, clear, Schulz's signature ink
- **Secondary**: Cream/Off-white (`#faf8f3`) — warm, paper-like, vintage comic stock
- **Accent 1 - Red** (`#e63946`) — urgency, warnings, Snoopy's iconic red doghouse
- **Accent 2 - Blue** (`#457b9d`) — calm, information, Charlie Brown's shirt
- **Accent 3 - Yellow** (`#f4d35e`) — highlights, happiness, Woodstock's color
- **Neutral Gray** (`#6c757d`) — secondary text, disabled states

**Emotional Intent**: Nostalgic comfort paired with professional reliability. The palette evokes 1960s newspaper comics while maintaining modern clarity.

### Layout Paradigm
**Comic Panel Grid** — instead of centered layouts, organize content as overlapping comic panels with visible borders and gutters. Use:
- Asymmetric panel sizing to create visual rhythm
- Thought bubbles for metadata/status indicators
- Speech bubbles for messages and alerts
- Gutter spacing (8-12px) between panels to suggest depth
- Occasional diagonal or skewed panels for dynamic energy

### Signature Elements
1. **Hand-Drawn Borders**: Slightly irregular black strokes (1-2px) around panels, cards, and sections
2. **Comic Speech/Thought Bubbles**: Used for alerts, tooltips, and contextual information
3. **Doodle Accents**: Small decorative line illustrations (e.g., Snoopy-inspired paw prints, simple stars) in margins or as section dividers
4. **Dot Pattern Background**: Subtle halftone or dot pattern in card backgrounds (reminiscent of comic printing)

### Interaction Philosophy
- **Immediate Feedback**: Buttons respond with slight scale/rotation on click (comic "pop" effect)
- **Hover Hints**: Subtle outline thickening or color shift on interactive elements
- **Playful Transitions**: Smooth 200-300ms animations that feel snappy, never sluggish
- **Status Indicators**: Use comic-style "!" and "?" symbols alongside color badges

### Animation
- **Button Press**: Scale 0.95 with 120ms ease-out + slight rotation (±1°) for playful "thud"
- **Panel Entrance**: Fade in + slight slide from left/top with 250ms ease-out (staggered by 50ms per panel)
- **Status Changes**: Color transition with 300ms ease-in-out; optional pulse effect for alerts
- **Hover Effects**: Outline thickens, shadow deepens, background lightens slightly (all 150ms)
- **Respect prefers-reduced-motion**: Disable animations for users with motion sensitivity

### Typography System
- **Display Font**: "Comic Sans" (or fallback to playful serif like "Courier New" with custom styling) for headers and titles — bold, distinctive, unmistakably comic
- **Body Font**: "Courier New" or monospace for readability and technical context (logs, code snippets)
- **Hierarchy**:
  - **H1** (32px, bold, Comic Sans): Main titles (Dashboard, Run Details)
  - **H2** (24px, bold, Comic Sans): Section headers (Limits, Coach, Timeline)
  - **H3** (18px, bold, Comic Sans): Card titles
  - **Body** (14px, regular, Courier New): Content text
  - **Small** (12px, regular, Courier New): Metadata, timestamps, secondary info
  - **Monospace** (12px, Courier New): Code, JSON, logs

### Brand Essence
**One-line positioning**: *A playful yet precise orchestrator for multi-agent AI workflows—where serious engineering meets comic-strip charm.*

**Personality Adjectives**: Approachable, Intelligent, Whimsical

### Brand Voice
- **Headlines**: Conversational, slightly playful but clear (e.g., "Oops! Guard blocked that call" instead of "Error: Guard limit exceeded")
- **CTAs**: Action-oriented with personality (e.g., "Let's go!" instead of "Submit", "Peek inside" instead of "View details")
- **Microcopy**: Warm, supportive tone (e.g., "You're running low on Claude tokens—switch to Codex?" instead of "Warning: insufficient tokens")
- **Example lines**:
  - "Snoopy's taking a nap—your loop is paused."
  - "Woodstock says: your 7-day limit resets in 3 hours!"

### Wordmark & Logo
**Logo Concept**: A minimalist line-drawn silhouette of Snoopy sitting on his doghouse (profile view), with a speech bubble containing a gear icon. Black outline on transparent background, no text. Dimensions: 40×40px for header, scalable.

### Signature Brand Color
**Red** (`#e63946`) — Snoopy's doghouse, urgency, and warmth. This red is unmistakably tied to the Peanuts universe and serves as the primary accent throughout the interface.

---

## Design Implementation Notes

### CSS Variables (index.css)
- `--primary`: `#1a1a1a` (black)
- `--primary-foreground`: `#faf8f3` (cream)
- `--accent`: `#e63946` (red)
- `--secondary`: `#457b9d` (blue)
- `--muted`: `#f4d35e` (yellow)
- `--destructive`: `#e63946` (red for errors)

### Component Styling
- **Cards**: Black 2px border, cream background, subtle dot pattern overlay
- **Buttons**: Black border, cream background, red text or red background with cream text
- **Inputs**: Cream background, black border, monospace font
- **Alerts**: Speech bubble shape with colored border (red for danger, yellow for warning, blue for info)
- **Dividers**: Hand-drawn line effect using SVG or CSS dashed border

### Responsive Behavior
- **Mobile**: Stack panels vertically, reduce gutter spacing to 6px, scale typography down 10%
- **Tablet**: 2-column panel layout, maintain gutter spacing
- **Desktop**: 3-column or asymmetric panel layout, full spacing

---

## Visual References
- Charles M. Schulz's Peanuts comic strip (1950s-2000s)
- Vintage newspaper comic aesthetic
- Hand-drawn UI design trends (Excalidraw, Whimsical)
