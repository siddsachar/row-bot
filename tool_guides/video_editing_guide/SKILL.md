---
name: video_editing_guide
display_name: Video Editing Guide
icon: "🎬"
description: Guidance for making videos with video and editing them with edit_video.
tools:
  - video_editing
tags: []
---
- A video of scenes (a demo, how-to, teaser or explainer: recordings,
  clips, title cards, a voice, music) is the video tool. A scene without a
  source is a title card, and it voices each line and lays the music
  itself: never draw cards, mix sound or run ffmpeg with scripts.
- A demo of a site or app in Row-Bot's browser: write the story first, 3-8
  steps with one spoken line each. Start browser_record, then before each
  step mark a beat with its note (a short title) and its line, do the step,
  and stay on it about as long as the line takes (the beat says how long);
  beat=work before a wait, cut for a stretch to leave out. Stop, then video
  action create with recording=rec-…: one scene per beat with its title,
  line and voice. The camera follows what was done (leave zoom empty).
- The video is the app, not slides: at most an opening and a closing card
  (3 s, a few words); titles go over the footage as a scene's card.
- Phone demos: browser_viewport phone before recording (taps show as
  touches); a 16:9 video sets the phone in a device, 9:16 fills the height.
- Its look and voice are the person's: the brand kit (colour, captions,
  framing, camera, voice, speed, music mood) sets every video, and a video
  may choose its own voice and voice_speed. Change them when asked.
- After make, read the checks and look at the review sheet with
  analyze_image. Fix what's off (a dull or long stretch: trim or speed it
  up; a line that doesn't match its picture: rewrite it) and make it again;
  it's done only when both are right.
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
- Never slow a voice to fill time (it sounds harsh): write more words, or cut
  the video shorter to fit what's said.
- A video given a voice is checked when it's made. "Needs attention" is not
  done: a quiet gap wants a line or a shorter part, and a picture held 10 s
  (20 s under the voice) wants that part cut shorter, sped up or zoomed.
  Fix it and make it again.
- The result is an MP4 in this chat with a player; do not paste it again.
  A long edit carries on in the background with its own progress and Stop:
  say so, and call edit_video with action status and its edit_id to wait.
- The first edit may ask to download ffmpeg; if the person says no or the
  chat is on Block, say what happened and stop.
- action frames saves stills (count, or times) as one contact sheet; to look
  at many pictures or a video's frames, use analyze_image with files and
  contact_sheet. action save copies a finished edit into the workspace
  (replacing a file there asks first).
