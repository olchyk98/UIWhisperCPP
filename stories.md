# UIWhisperCPP - User stories

## Product
A native macOS desktop app that transcribes audio and video files fully offline: no cloud, no
accounts, no API keys. Built so a non-technical person can open it and know what to do. Engines:
Parakeet v3 (parakeet-mlx, Apple Silicon only, default) and whisper.cpp (four model sizes).
Optional speaker separation runs the pyannote 3.1 pipeline on ONNX Runtime in a separate process.
Ships as a self-contained .app (PyInstaller, bundled ffmpeg) that needs macOS 15 or newer; also
runs from source with uv. Models download once on first use, from public sources, through a safe
downloader (temporary file, timeout, size check). Updates come from GitHub releases of
`olchyk98/UIWhisperCPP` by default. A debug log at `~/Library/Logs/UIWhisperCPP/app.log` records
everything, including the engines' raw output.

## Starting the app

**Scenario: Mac too old for the app**
- **Given** the Mac runs a macOS version older than 15
- **When** the user opens UIWhisperCPP from Finder
- **Then** macOS refuses to open it with its own message naming macOS 15.0 as the requirement
- **And** if the app is started another way, a native alert titled "macOS update required" says
  the app needs macOS 15.0 or newer, names the installed version, says this is not a bug, and
  offers "Open Software Update" or "Quit"
- **And** a Mac that reports the compatibility version 10.16 is checked by its real version

**Scenario: First launch**
- **Given** a supported Mac and no files added yet
- **When** the user opens the app
- **Then** the large right pane shows "Drop a recording here", a line explaining that audio or
  video works (MP3, M4A, WAV, MP4 and more), that the text appears there and is saved next to the
  original file, and that everything stays on this Mac
- **And** below it a blue "Choose Files…" button and an "or choose a folder" link
- **And** until the first file has been transcribed successfully, a hint at the bottom says the
  speech model downloads once the first time (a few minutes) and then everything works offline
- **And** the left pane reads "No files yet" under a "Files" heading with a "+" button
- **And** the window follows the system light or dark appearance, also when it changes live

**Scenario: Choices are remembered**
- **Given** the user changed the model, language, "Separate speakers" or the number of people
- **When** they reopen the app
- **Then** the same choices are selected again (defaults: Parakeet v3, Auto-detect language,
  speakers off, 2 people)
- **And** a damaged or nonsensical saved value never stops the app from opening; it falls back
  to the default

## Adding files

**Scenario: Supported formats**
- **Given** the app is open
- **When** the user adds files
- **Then** these types are accepted, in any letter case: WAV, MP3, M4A, WMA, FLAC, OGG, OGA, OPUS,
  AAC, AIF, AIFF, AIFC, CAF, AMR, AC3, M4B, MKA, WV, WEBM, MP4, MOV, MKV, 3GP and AVI
- **And** for video files only the audio track is transcribed

**Scenario: Adding by drag and drop**
- **Given** the app is open
- **When** the user drags files or folders over the window
- **Then** a blue rounded frame with "Drop to transcribe" covers the window
- **And** on drop every supported file among them is added and queued; a folder contributes the
  supported files directly inside it, not sub-folders
- **And** dropping something that isn't a file on this Mac shows "Only files on this Mac can be
  added. Download them first."

**Scenario: Adding through buttons, menus or shortcuts**
- **Given** the app is open
- **When** the user clicks "Choose Files…" on the welcome screen, the "+" button (menu "Add
  Files…" / "Add Folder…"), or uses Cmd+O / Cmd+Shift+O
- **Then** a file or folder picker opens in the last folder used
- **And** the chosen supported files are queued; cancelling adds nothing

**Scenario: Nothing usable was added**
- **Given** the app is open
- **When** the user adds only unsupported files, an empty folder, or a folder the app may not read
- **Then** nothing is queued and a note pops up at the bottom of the window for a few seconds: "Nothing to add:
  those aren't audio or video files." or, for a blocked folder, "Couldn't open "<folder>". Allow
  access in System Settings > Privacy & Security, then try again."

**Scenario: Adding a file that is already waiting**
- **Given** a file is already waiting or being transcribed
- **When** the user adds the same file again
- **Then** it is not added a second time

## Transcribing

**Scenario: Files are processed one at a time**
- **Given** several files are queued
- **When** the first one starts
- **Then** files are transcribed one after another in the order added, starting immediately
- **And** each file uses the model, language, speaker setting and output format selected at the
  moment it starts; changing a setting while a file runs shows "Changes apply to the next file.
  Use Transcribe Again to redo this one."
- **And** each row shows the file name and a status: "Waiting", the current step with its
  percentage on the right and a thin blue progress bar, "✓ Done" in green, "Didn't finish" in
  red, or "Stopped"
- **And** while a step has no percentage yet (getting ready, loading a model), the bar animates
- **And** the window title reads "Transcribing 2 of 4 - UIWhisperCPP" while working

**Scenario: First use of a model downloads it with progress**
- **Given** the chosen model (or the speaker files) isn't on this Mac yet
- **When** a file starts
- **Then** the status shows e.g. "Downloading Parakeet v3 (1.2 GB of 2.5 GB, one-time) 48%"
- **And** an interrupted, truncated or broken download is never kept or loaded: the next try
  downloads it fresh
- **And** "Stop" works instantly during a download too, even when the server doesn't answer
- **And** without internet the file fails with "Couldn't download the <model>. It needs an
  internet connection the first time only - check your connection and try again."

**Scenario: Watching a transcript appear**
- **Given** a file is being transcribed
- **And** the user has not picked a different file in the list
- **When** new lines are produced
- **Then** the running file is selected and its lines appear live in the right pane, formatted as
  they will be saved, with timestamps and speaker tags in a muted color and the words in full
  color, in a column no wider than a comfortable reading width
- **And** both engines stream: Whisper line by line, Parakeet in batches every couple of minutes
  of audio processed (each batch holds the sentences that can no longer change)
- **And** the pane stays scrolled to the newest line unless the user scrolled up to read
- **And** under the file name a muted line shows what it runs with, e.g. "Parakeet v3 · language
  detected automatically · 2 speakers"
- **And** long file names are shortened with "…" in the middle (full name on hover) and never
  push the panes around, at any window size down to the minimum

**Scenario: Stopping a file**
- **Given** a file is being transcribed
- **When** the user clicks "Stop" in the pane header (or "Stop" in the row's right-click menu)
- **Then** the status reads "Stopping…" and the file stops at the next safe point
- **And** the text so far is kept and saved; the pane says "Stopped. The text so far is saved as
  <name> next to the original file" and offers "Transcribe Again" and "Remove"
- **And** while Parakeet runs and no text has arrived yet, the pane explains that Parakeet adds the
  text in batches, about every two minutes of audio
- **And** the next queued file starts

**Scenario: The saved transcript**
- **Given** a file finished transcribing
- **When** the user looks next to the audio file
- **Then** there is a file with the same name and the chosen extension (.txt or .srt)
- **And** when another audio or video file with the same name sits in that folder (e.g.
  `talk.mp3` and `talk.mp4`, also if only the letter case differs), the original extension is
  kept (`talk.mp3.txt`) so transcripts never overwrite each other
- **And** the pane says "Saved as <name> next to the original file", where the name is a link
  that opens the transcript (full path on hover), and offers "Copy Text" (briefly "Copied ✓") and
  "Show in Finder"; double-clicking the row also opens the transcript
- **And** while Whisper partial text is saved every couple of seconds, so a crash keeps most of it

**Scenario: The transcript name is already taken**
- **Given** a file with the transcript's name already exists next to the recording
- **When** that recording's turn comes
- **Then** a dialog says "A transcript of this recording already exists." and asks to "Replace"
  it, "Keep Both" (default: the new one is saved as "<name> (2).txt", or the next free number) or
  "Skip This File"
- **And** when more files are waiting, "Do the same for the other files" applies the choice to
  the rest of this batch
- **And** "Transcribe Again" reuses the name chosen the first time without asking again, unless
  the file type changed and the new name is taken
- **And** two files in the same list never write to the same transcript

**Scenario: A file that can't be read**
- **Given** a queued file is damaged, empty, a video without sound, moved away, blocked by macOS
  privacy settings, or an iCloud/network file that doesn't open within two minutes
- **When** its turn comes
- **Then** it fails with a calm boxed message such as ""clip.mp4" has no sound to transcribe." or
  ""x.wma" can't be opened. It may be damaged or in an unusual format. If it won't play in
  QuickTime either, it can't be transcribed."
- **And** the box offers "Try Again" and "Remove", and the files after it are transcribed normally
- **And** unexpected engine errors never appear as raw text: the box says "Something went wrong
  with this file. Details are in Help > Debug Log." and the details go to the debug log

**Scenario: The transcript can't be saved**
- **Given** the recording's folder is read-only or the disk is full
- **When** the file finishes
- **Then** the text stays visible in the pane and the row reads "Couldn't save"
- **And** a box explains "Couldn't save the transcript next to the recording." with "Save As…",
  "Try Again" and "Remove"; "Copy Text" still works
- **And** the queue carries on with the next file
- **And** quitting while such text is unsaved asks first: "The text of <name> couldn't be saved
  yet." with "Go Back" (default, selects that file) or "Quit Without Saving"
- **And** a write that fails never empties or damages a file that was already there

**Scenario: No speech in a file**
- **Given** a file contains only silence or music
- **When** it finishes
- **Then** it is marked done with the note "No speech was found in this file, so nothing was
  saved." (whisper's "[BLANK_AUDIO]"-style markers are never written as text)

**Scenario: Managing the list**
- **Given** files are in the list
- **When** the user right-clicks a row
- **Then** they can "Stop" (running), "Open Transcript" (when saved), "Show in Finder",
  "Transcribe Again" (finished files) and "Remove from List" (any file that is not running)
- **And** a quiet "Clear" link next to the "Files" heading (and File > Clear Finished) removes all
  finished rows from the list (never from disk) once any exist

**Scenario: Quitting while transcribing**
- **Given** a file is being transcribed
- **When** the user closes the window
- **Then** a dialog names the file and how many are waiting, says that file won't be finished,
  the waiting ones won't be transcribed and text already saved stays on disk
- **And** "Keep Transcribing" (the default) keeps working; "Quit Anyway" stops and quits

## Models

**Scenario: Choosing a model**
- **Given** the app is open
- **When** the user opens the model menu
- **Then** they see "Parakeet v3 - fast and accurate, recommended (2.5 GB)" (the default), then
  "Whisper Large v3 - most accurate, slowest (3.1 GB)", "Whisper Medium - accurate, slower
  (1.5 GB)", "Whisper Small - balanced speed and accuracy (490 MB)" and "Whisper Base - quick
  draft, least accurate (150 MB)"
- **And** once closed, the menu shows just the short name ("Parakeet v3", "Whisper Large v3",
  "Whisper Medium", "Whisper Small", "Whisper Base")
- **And** the first run of a model downloads it once and then works offline

**Scenario: Choosing the language**
- **Given** a Whisper model is selected
- **When** the user opens the language menu
- **Then** they can pick "Auto-detect language" (default), English, Ukrainian, Swedish, Russian,
  German, French, Spanish, Italian, Polish, Dutch or Portuguese
- **And** while Parakeet is selected the language menu is hidden, since Parakeet recognises the
  language by itself

**Scenario: Parakeet on a Mac that can't run it**
- **Given** the Mac has an Intel processor
- **When** the user opens the model menu
- **Then** Parakeet is greyed out with "- needs a Mac with Apple Silicon" and a tooltip
- **And** a Whisper model is selected by default instead

**Scenario: Parakeet is set up automatically**
- **Given** the packaged app, which already includes Parakeet
- **Or** a source checkout on Apple Silicon where Parakeet's package is missing
- **When** the user transcribes with Parakeet
- **Then** it just works; for a source checkout the row first shows "Setting up Parakeet
  (one-time, about a minute)…" and installs it automatically
- **And** the user is never told to run an install command; if setup fails, the file fails with
  a message suggesting a Whisper model at the bottom of the window

## Speaker separation

**Scenario: Separating speakers**
- **Given** "Separate speakers" is ticked
- **Then** a "People:" box appears next to it (default 2; "Not sure" lets the app guess); it is
  hidden while the box is unticked
- **When** a file finishes transcribing
- **Then** a second step "Separating speakers…" runs with its own progress
- **And** each line is tagged with a speaker number starting at 0, and a line where the voice
  changes mid-sentence is split at the word where it changes (Parakeet only)
- **And** voices that sound identical are not split apart even if the count allows it
- **And** the first use downloads the speaker files once, with progress and no account needed

**Scenario: Speaker separation fails**
- **Given** "Separate speakers" is ticked
- **When** the separation step crashes, stops responding for ten minutes, or can't download its
  files
- **Then** the transcript is still saved, without speaker labels, and the file is marked done in
  amber with the note "Saved without speaker labels: separating speakers failed (<reason>)."
- **And** damaged speaker files are deleted so the next try downloads them fresh

## Output format

**Scenario: Customizing the output**
- **Given** the user opens Settings (Cmd+, or the "Settings" button) on the "Output" tab
- **When** they change any option
- **Then** it applies immediately to files that start afterwards ("Changes apply to files
  transcribed from now on.") and is remembered between launches
- **And** a PREVIEW box shows two sample lines in the chosen format
- **And** the options are: "File type" Plain text (.txt, default) or Subtitles for video players
  (.srt); "Timestamps" Start and end (default), Start only or No timestamps; "Time detail"
  With milliseconds (00:01:05.250, default) or To the second (00:01:05); "Label speakers (when Separate
  speakers is on)", on by default

**Scenario: Plain text format**
- **Given** "File type" is Plain text
- **When** a transcript is saved
- **Then** each line reads `[00:00:01.200 --> 00:00:04.850]: text` with start and end,
  `[00:00:01.200]: text` with start only, or just the text with no timestamps
- **And** with speaker labels the tag follows the timestamp, e.g. `[00:00:01.200] [Speaker 0]:
  text`, or `[Speaker 0]: text` without timestamps
- **And** "To the second" drops the milliseconds (`00:00:01`)

**Scenario: Subtitle format**
- **Given** "File type" is Subtitles (.srt)
- **When** a transcript is saved
- **Then** it is a standard numbered SRT file with `00:00:01,200 --> 00:00:04,850` ranges
- **And** the Timestamps and Time detail options are disabled with the note "Subtitles always
  include start and end times."
- **And** speaker labels, when on, prefix the text as `[Speaker 0] text`

**Scenario: Finished transcripts keep their format**
- **Given** a file finished and was saved in one format
- **When** the user changes the output options afterwards
- **Then** that file's transcript in the right pane still shows the format it was saved in

## Updates

**Scenario: Checking for updates**
- **Given** the user opens Settings > "Updates" (or the app menu "Check for Updates…")
- **When** they click "Check for Updates"
- **Then** the app asks GitHub for the latest release of the update source
- **And** it shows "You have the latest version (<version>)." or "Version <x> is available
  (<size> MB download)." with an "Update and Restart" button
- **And** the app never checks on its own

**Scenario: Installing an update**
- **Given** a newer release is available and nothing is being transcribed
- **When** the user clicks "Update and Restart"
- **Then** a progress bar and "Downloading the update… <percent>% (<done> of <total> MB). Keep
  this window open." appear
- **And** the download must match the checksum GitHub publishes, be this app and pass its code
  signature check; otherwise it is refused with a short message and nothing is replaced
- **And** after "Installing… The app will close and reopen by itself." the app replaces itself in
  the same place and opens again; if the swap fails, the previous version is put back
- **And** closing the Settings window during the download cancels it

**Scenario: An update finishes downloading while files are queued**
- **Given** the update download started while idle
- **When** files were added during the download and are still being transcribed when it finishes
- **Then** the app says "The update is ready. It installs and restarts the app automatically when
  the current files are finished and saved." and installs only once the queue is empty and no
  transcript is waiting to be saved

**Scenario: Update blocked**
- **Given** a newer release is available
- **When** a file is being transcribed, the app runs from source, or its folder is not writable
- **Then** the app doesn't update and says why: "Finish transcribing first, then update.", to use
  git pull, or to drag the app into Applications and open it from there

**Scenario: Changing the update source**
- **Given** the user is on Settings > "Updates"
- **When** they open "Advanced" and enter another repository as owner/repo or a GitHub URL
- **Then** future checks use it, a red note warns that it isn't the official source and to use
  only a trusted repository, and "Use Default" restores `olchyk98/UIWhisperCPP`
- **And** "Advanced" stays open while a non-default source is set
- **And** an invalid entry shows "'<text>' isn't a GitHub repository. Use the form owner/repo...";
  a repository without releases, a release without an app download, a rate limit, a captive
  Wi-Fi page and no internet each get their own plain message

## Troubleshooting

**Scenario: Opening the debug log**
- **Given** someone wants to see what the app is really doing
- **When** they click the small grey "Debug log" link at the bottom of Settings, or choose Help >
  Debug Log (Option-Cmd-L)
- **Then** a "Debug Log" window shows the live log: app start (version, macOS, chip), each file's
  settings, every status change, model download requests, full error details and the engines'
  raw console output
- **And** it offers "Follow new lines", "Clear view", "Copy all" and "Show log file"; the same log
  is kept in `~/Library/Logs/UIWhisperCPP/app.log` (rotated, so it stays small)
