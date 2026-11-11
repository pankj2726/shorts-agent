---
name: generative-scene-directing
description: Autonomous prompt directing and cinematic motion conversion for 9:16 vertical generative AI scenes (Pollinations Flux, Midjourney, Fal.ai) using FFmpeg dynamic camera pushes.
version: 1.0.0
author: Shorts Forge Team
---

# Generative Scene Directing Skill

## Overview
This skill instructs autonomous visual agents on how to construct prompt directives for vertical 9:16 generative image/video models and convert them into broadcast-quality cinematic motion clips without relying on third-party stock footage.

## Prompt Engineering Blueprint (Vertical 9:16)
When directing visual generators for YouTube Shorts, the agent MUST format prompts following this 5-token structure:

1. **Composition & Aspect Ratio Anchor:**
   `cinematic 8k vertical photography, award-winning documentary, 9:16 vertical portrait`
2. **Subject & Micro-Action:**
   `extreme close-up of [Subject], [Specific action or movement], piercing eyes, intricate textures`
3. **Lighting & Atmosphere:**
   `volumetric mist, dramatic rim lighting, cold mountain blizzard, cinematic shadows`
4. **Camera & Lens Language:**
   `35mm anamorphic lens, shallow depth of field, sharp focus, slow shutter`
5. **Quality Negative Constraints:**
   `photorealistic, no text, no watermark, no logo, highly detailed 8k`

### Example Prompt
```text
cinematic 8k vertical photography, extreme close-up of a snow leopard in a high altitude Himalayan blizzard, piercing grey-blue eyes looking into camera, snowflakes melting on whiskers, cold morning rim lighting, shallow depth of field, 35mm anamorphic lens, photorealistic, 9:16 portrait, no logo
```

## FFmpeg Cinematic Motion Formula (Dynamic Camera Push-In)
To transform a static 1080×1920 vertical still into a 3.5s fluid documentary camera move:

```bash
ffmpeg -y -loop 1 -i scene.jpg \
  -vf "zoompan=z='min(zoom+0.0012,1.20)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=105:s=1080x1920:fps=30,format=yuv420p" \
  -t 3.5 -c:v libx264 -preset veryfast -pix_fmt yuv420p output_clip.mp4
```

## Anti-Slop Visual Rules
- NEVER generate abstract geometric blobs or generic corporate people smiling.
- ALWAYS maintain subject and color continuity between scenes.
- Cut on the beat of the voiceover audio impact.
