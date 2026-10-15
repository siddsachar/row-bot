---
name: video_editing_guide
display_name: Video Editing Guide
icon: "🎬"
description: Guidance for editing videos and taking stills with edit_video.
tools:
  - video_editing
tags: []
---
- edit_video edits videos the person already has: recordings (rec-…),
  earlier edits (edit-…), files in this chat by name, or workspace paths.
  It never generates video (that is generate_video, when it is on).
- Put the whole edit in one call: inputs in order (several are joined),
  then steps in order. Each step's times are seconds on the video as it is
  after the steps before it (after a trim, 0 is the trim's start).
- Demo videos: trim the dead time, speed up waiting or typing with
  {op: speed, factor: 2-8, start, end} (it shows a "2×" label unless
  label is false), add a badge with {op: text}, zoom in on a detail with
  {op: zoom, region: [x, y, width, height], start, end}, and caption with
  {op: captions, text: <SRT>}. Regions are fractions of the picture.
- The result is an MP4 in this chat with a player; do not paste it again.
  A long edit carries on in the background with its own progress and Stop:
  say so, and call edit_video with action status and its edit_id to wait.
- The first edit may ask to download ffmpeg; if the person says no or the
  chat is on Block, say what happened and stop.
- action frames saves stills (count, or times) as one contact sheet; to look
  at many pictures or a video's frames, use analyze_image with files and
  contact_sheet. action save copies a finished edit into the workspace
  (replacing a file there asks first).
