---
name: video_editing_guide
display_name: Video Editing Guide
icon: "🎬"
description: Making videos (video first, edit_video for edits); present the finished video with present; frames and review sheets are working files.
tools:
  - video_editing
tags: []
---
- A video of scenes (a demo, how-to, teaser, explainer, vlog or short
  film: recordings, clips, AI shots, photos, title cards, a voice, music)
  starts with the video tool. A scene's source is a video or a picture (a
  picture is held with a slow push in), from this chat, the workspace or a
  full path on this computer, used where it is, never copied. A scene
  without a source is a title card, and it voices each line and lays the
  music itself.
- A new video of several scenes is a storyboard first: video action create
  with a logline and each scene's footage (clip, picture, ai, card or
  screen), what it shows, its line, length and music cue. Nothing is made.
  Footage that doesn't exist yet (an AI shot) has no source until you make
  it. Show the storyboard; once the person approves, or at once when they
  said to make the video, make the planned footage, change those scenes with
  their sources, then video action make. A request to plan, draft or
  storyboard a video, or to work on one together, gets the storyboard.
  A comment on a card comes to you as their message: change that scene.
  Quick edits and a video of one scene are made at once.
- For footage not made yet its words are enough; make a frame with
  generate_image only when the person asked for pictures in the storyboard.
- Render the final through the video tool whenever it can do the job. Only
  when a look needs more than it offers, a script of your own (ffmpeg,
  Pillow) is the fallback: check still frames of each scene first, render
  the whole film once they're right, hold it to the same checks, and
  present the result once it passes them.
- A demo of a site or app in Row-Bot's browser: write the story first, 3-8
  steps with one spoken line each. Start browser_record, then before each
  step mark a beat with its note (a short title) and its line, do the step,
  and stay on it about as long as the line takes (the beat says how long);
  beat=work before a wait, cut for a stretch to leave out. Stop, then video
  action create with recording=rec-…: a storyboard of one scene per beat
  with its title, line and voice. The camera follows what was done (leave
  zoom empty).
- The video is the app, not slides: at most an opening and a closing card
  (3 s, a few words); titles go over the footage as a scene's card.
- Phone demos: browser_viewport phone before recording (taps show as
  touches); a 16:9 video sets the phone in a device, 9:16 fills the height.
- Its look and voice are the person's: the brand kit (colour, captions,
  framing, camera, voice, speed, music mood, loudness) sets every video, and a
  video may choose its own voice and voice_speed. When the person says how their
  videos should look ("full screen", a voice, a mood), save it with video
  action brand, so the next video starts that way.
- Sound: music 'fit' is made to the video's length in the brand kit's mood
  (or music_mood), changing where the music cue does, so make_music isn't
  needed. A scene's speaker says its line in that character's voice (a voice
  in cast becomes a saved character's voice in every video, so give one only
  when the person chose it). Effects and ambience go on a scene.
  A montage cuts on the music's beat with on_beat. A change to the sound
  alone mixes it again; the picture isn't drawn again.
- Captions only when the person asks for them: a narrated video needs none.
- After make, read the checks: they list what to fix by scene and time, and
  already include one look at the review sheet, so don't look again with
  analyze_image. Fix what they list (soft: a sharper clip or less zoom;
  words over the action: a zoom that shows it higher, or shorter words; a
  dull or long stretch: trim or speed it up; a line that doesn't match its
  picture: rewrite it) and make it again; it's ready to share only when the
  checks pass.
- Once the checks pass, the video is finished: present it with present in
  the reply that delivers it. Frames, contact sheets, review sheets and raw
  recordings you'll cut into the video are working material for you, not
  something to present. Once a file is presented, don't present or paste it
  again; send it to a device or app only when the person asks.
- edit_video edits videos the person already has: recordings (rec-…),
  earlier edits (edit-…), files in this chat by name, or workspace paths.
  It never generates video (that is generate_video, when it is on).
- A screen recording has no sound, so a cut made only from recordings is
  silent, there and on X. A video for people to watch or post is made with
  the video tool, with a line and voice per scene (and music if wanted). If a
  result says it has no sound, never hand it on as a finished demo.
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
- Write each line as its caption should read: numbers as digits ("8,848.86 m",
  "2 minutes"); the voice reads them aloud. Never spell numbers out.
- A video given a voice is checked when it's made. "Needs attention" is not
  done: a quiet gap wants a line or a shorter part, and a picture held 10 s
  (20 s under the voice) wants that part cut shorter, sped up or zoomed.
  Fix it and make it again.
- The result is an MP4 in this chat: present it when it's the final. A
  long edit carries on in the background with its own progress and Stop:
  say so, and call edit_video with action status and its edit_id to wait.
- The first edit may ask to download ffmpeg; if the person says no or the
  chat is on Block, say what happened and stop.
- action frames saves stills (count, or times) as one contact sheet; to look
  at many pictures or a video's frames, use analyze_image with files and
  contact_sheet. action save copies a finished edit into the workspace
  (replacing a file there asks first).
