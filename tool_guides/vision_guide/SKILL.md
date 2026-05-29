---
name: vision_guide
display_name: Vision Guide
icon: "👁"
description: Guidance for using webcam, screen capture, and image analysis.
tools:
  - vision
tags: []
---
- You have DIRECT ACCESS to the user's webcam and screen through the
  analyze_image tool. You CAN see — this is not hypothetical. When the user
  says anything like 'what do you see', 'look at this', 'can you see me',
  'what's in front of me', 'describe what you see', or any variation asking
  you to look or see, IMMEDIATELY call analyze_image — do NOT ask for
  clarification, do NOT say you can't see, do NOT ask them to describe it.
  Just call the tool. Use source='camera' by default. Use source='screen'
  when they mention screen, monitor, display, or desktop.
  Use source='file' with file_path when the user asks about a specific image
  file in the workspace (e.g. 'describe diagram.png', 'what's in photo.jpg').
  Pass the user's question as the argument (or 'Describe everything you see'
  if the question is vague like 'what do you see').
  When the user attaches or pastes images in chat, they are auto-analyzed
  when the selected Vision model succeeds, and their descriptions appear in
  the message context — do NOT call analyze_image for these successful
  auto-analyses. If the context says Vision analysis failed, you may call
  analyze_image when the user still wants the image inspected. Only use
  source='file' for files that already exist in the workspace folder.
