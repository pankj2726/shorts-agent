---
name: anti-slop-evaluation
version: 0.1.0-draft
status: draft
owner: agent-2-anti-slop
last_verified: 2026-11-10
confidence: medium
evidence_level: framework-designed
---

# Skill: Anti-Slop Evaluation Framework

**Purpose:** A structured rubric for evaluating short-form video content across 6 mandatory stages, producing evidence-based PASS / NEEDS REVISION / REJECT verdicts.

---

## Evaluation Input Contract

```json
{
  "video_id": 1,
  "script": "...",
  "storyboard_path": "...",
  "assets": ["path1", "path2"],
  "render_path": "temp/short_1/video_1_final.mp4",
  "render_metadata": {
    "resolution": "1080x1920",
    "duration_s": 30.6,
    "loudness_lufs": -14.9,
    "caption_events": 61
  }
}
```

---

## Evaluation Output Contract

```json
{
  "video_id": 1,
  "stage": "A",
  "verdict": "PASS",
  "overall_score": 82,
  "dimension_scores": {
    "originality": 80,
    "authenticity": 85,
    "storytelling": 85,
    "visual_richness": 70,
    "emotional_impact": 80,
    "pacing": 85,
    "audio_quality": 90,
    "technical_consistency": 90,
    "audience_relevance": 80
  },
  "problems": [],
  "corrections": [],
  "evaluator": "agent-2-anti-slop",
  "evaluated_at": "2026-11-10T10:00:00Z"
}
```

---

## Stage A — Script Quality Review

### Pass Criteria
- Hook ≤ 12 words: **hard requirement**
- No banned phrases (see `humanized-scriptwriting` skill)
- Duration 20–45 seconds (50–112 words at 150 wpm)
- Title ≤ 60 characters
- Delivers specifically on hook promise
- One core insight (not three loosely connected tips)
- Factual claims have source attribution

### Scoring
| Sub-criterion | Weight | Score |
|---------------|--------|-------|
| Hook quality | 30% | |
| Authenticity/no-slop | 30% | |
| Narrative arc | 25% | |
| Technical compliance | 15% | |

### Automatic REJECT Triggers (Stage A)
- Hook > 12 words
- Contains banned phrases from BANNED_TOPICS list
- Makes unverifiable factual claims without attribution
- Plagiarized from known source

---

## Stage B — Storyboard & Scene Continuity

### Evaluation Questions
1. Does each scene logically follow the previous one?
2. Are visual transitions appropriate (not jarring without purpose)?
3. Does the visual content match the voiceover at each moment?
4. Is there a visual payoff for the hook?

### Scoring
| Sub-criterion | Weight |
|---------------|--------|
| Scene logic | 35% |
| Visual-audio alignment | 35% |
| Transition quality | 30% |

---

## Stage C — Generated Asset Review

### Evaluation Questions
1. Are assets 1080×1920 portrait format?
2. Do assets visually relate to the script content?
3. Are there obvious AI generation artifacts (distorted text, extra fingers, etc.)?
4. Is licensing documented? (CC0, MIT, Pollinations ToS, procedural)

### Scoring
| Sub-criterion | Weight |
|---------------|--------|
| Resolution compliance | 20% |
| Content relevance | 40% |
| Generation quality | 25% |
| Licensing clarity | 15% |

---

## Stage D — Video & Motion Quality

### Technical Pass Criteria (Hard)
- Resolution: exactly 1080×1920
- Duration: 20–60 seconds
- No dropped frames
- Subtitle events > 0

### Scoring
| Sub-criterion | Weight |
|---------------|--------|
| Technical compliance | 40% |
| Motion quality | 30% |
| Subtitle sync | 30% |

---

## Stage E — Audio & Synchronization

### Technical Pass Criteria (Hard)
- Loudness: −14 LUFS ± 2 (i.e., −12 to −16 LUFS)
- No clipping (peak ≤ −1 dBFS)
- Voice intelligibility: clearly understandable

### Scoring
| Sub-criterion | Weight |
|---------------|--------|
| Loudness compliance | 30% |
| Voice clarity | 35% |
| Caption-audio sync | 35% |

---

## Stage F — Final Content Integrity

### Evaluation Questions
1. Does the video deliver on its opening hook's promise?
2. Is the content authentic and non-misleading?
3. Does it have replay value?
4. Is it platform-compliant (no policy violations)?
5. Is synthetic media disclosure required? (AI-generated voice/visuals → YES)

### Scoring
| Sub-criterion | Weight |
|---------------|--------|
| Hook delivery | 30% |
| Authenticity | 25% |
| Platform compliance | 25% |
| Replay value | 20% |

---

## Overall Score Calculation

```
overall_score = (
  originality * 0.15 +
  authenticity * 0.15 +
  storytelling * 0.15 +
  visual_richness * 0.10 +
  emotional_impact * 0.10 +
  pacing * 0.10 +
  audio_quality * 0.10 +
  technical_consistency * 0.10 +
  audience_relevance * 0.05
)
```

**Minimum passing score:** 70/100 overall, no dimension below 50.

---

## Verdict Decision Table

| Condition | Verdict |
|-----------|---------|
| All hard criteria pass AND overall ≥ 70 AND no dimension < 50 | PASS |
| Hard criteria pass BUT overall 50–69 OR any dimension 30–49 | NEEDS REVISION |
| Any hard criterion fails OR overall < 50 OR any dimension < 30 | REJECT |

---

## Version History

| Version | Date | Change |
|---------|------|--------|
| 0.1.0-draft | 2026-11-10 | Initial framework from project brief and Phase 1–3 QA experience |
