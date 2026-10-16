---
name: video_guide
display_name: Video Guide
icon: "🎬"
description: Guidance for generating videos and animating images.
tools:
  - video_gen
tags: []
---
- You can generate short video clips from text descriptions using
  generate_video, or animate a still image into a video using animate_image.
- Use generate_video when the user asks you to create, produce, or make a
  video, clip, animation, or motion content from a text description.
  Provide a detailed prompt including subject, action, style, camera motion,
  and ambiance. Short cinematic prompts work best.
- Use animate_image when the user wants to bring a still image to life — the
  last generated image, an attached/pasted image, or a file on disk. Describe
  how the image should move or animate.
- Video generation takes 1–6 minutes depending on the provider and settings.
  Let the user know it may take a moment. Do not make multiple video requests
  in parallel.
- If a provider returns a validation error such as INVALID_ARGUMENT, do not
  retry the same request unchanged. Surface the provider error once, then
  adjust parameters or switch providers.
- Default settings: 8 seconds, 16:9 aspect ratio, 720p resolution. Only
  change these if the user explicitly requests different values.
- Google Veo supports 4, 6, or 8 second durations. 1080p and 4k require
  8 seconds. Aspect ratios: 16:9 or 9:16. Generates audio natively.
- For Google Veo in this release, do not infer or force person-generation
  settings from the prompt. Let the provider defaults apply.
- xAI supports 1–15 second durations. Aspect ratios: 1:1, 16:9, 9:16, 4:3,
  3:4, 3:2, 2:3. Resolutions: 480p or 720p.
- Generated videos are saved as MP4 files in the thread media folder and
  displayed inline in chat.
- Do NOT promise video extension, reference-image workflows, or fal-backed
  models — these are not available in this version. Editing a video the
  person already has (trim, join, speed, captions) is edit_video.
