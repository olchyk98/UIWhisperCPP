# UIWhisperCPP - User stories

## Product
A native macOS desktop app that transcribes audio and video files fully offline: no cloud, no
accounts, no API keys. Engines: Parakeet v3 (parakeet-mlx, Apple Silicon only, default) and
whisper.cpp (four model sizes). Optional speaker separation runs the pyannote 3.1 pipeline on ONNX
Runtime in a separate process. Ships as a self-contained .app (PyInstaller, bundled ffmpeg) that
needs macOS 15 or newer; also runs from source with uv. Models download once on first use, from
public sources. Updates come from GitHub releases of `olchyk98/UIWhisperCPP` by default.

## Starting the app

**Scenario: Mac too old for the app**
- **Given** the Mac runs a macOS version older than 15
- **When** the user opens UIWhisperCPP from Finder
- **Then** macOS refuses to open it with its own message naming macOS 15.0 as the requirement
- **And** if the app is started another way, a native alert titled "macOS update required" says
  the app needs macOS 15.0 or newer, names the installed version, says this is not a bug, and
  offers "Open Software Update" or "Quit"

**Scenario: Opening the app**
- **Given** a supported Mac
- **When** the user opens the app
- **Then** a two-pane window appears: a FILES list on the left with "Add files..." and "Add
  folder..." buttons, the transcript pane on the right, and a bottom bar with the model, language,
  "Separate speakers" with a speaker count, and "Settings"
- **And** the empty file list shows a dashed drop area reading "Drop audio or video files here"
- **And** the right pane reads "Select a file to see its transcript"
- **And** the window follows the system light or dark appearance, also when it changes live

**Scenario: Choices are remembered**
- **Given** the user changed the model, language, "Separate speakers" or the speaker count
- **When** they reopen the app
- **Then** the same choices are selected again

## Adding files

**Scenario: Supported formats**
- **Given** the app is open
- **When** the user adds files
- **Then** these types are accepted, in any letter case: WAV, MP3, M4A, WMA, FLAC, OGG, OGA, OPUS,
  AAC, AIF, AIFF, AIFC, CAF, AMR, AC3, M4B, MKA, WV, WEBM, MP4, MOV, MKV, 3GP and AVI
- **And** for video files only the audio track is transcribed

**Scenario: Adding by drag and drop**
- **Given** the app is open
- **When** the user drops files or folders anywhere on the window
- **Then** every supported file among them is added to the list and queued
- **And** a dropped folder contributes the supported files directly inside it, not sub-folders

**Scenario: Adding through dialogs or shortcuts**
- **Given** the app is open
- **When** the user clicks "Add files..." (Cmd+O) or "Add folder..." (Cmd+Shift+O)
- **Then** a file or folder picker opens in the last folder used
- **And** the chosen supported files are queued; cancelling adds nothing

**Scenario: Nothing usable was added**
- **Given** the app is open
- **When** the user adds only unsupported files or an empty folder
- **Then** nothing is queued and a note "No supported audio files found there." appears above
  the Add buttons for a few seconds

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
  moment it starts
- **And** each row shows the file name and a status: "Waiting", the current step with a
  percentage and a thin progress bar, green "Done", or red "Failed"
- **And** while a step has no percentage yet (loading a model, preparing), the bar animates

**Scenario: Watching a transcript appear**
- **Given** a file is being transcribed
- **And** the user has not picked a different file in the list
- **When** new lines are produced
- **Then** the running file is selected and its lines appear live in the right pane, formatted as
  they will be saved
- **And** both engines stream: Whisper line by line, Parakeet in batches every couple of minutes
  of audio processed (each batch holds the sentences that can no longer change)
- **And** the pane stays scrolled to the newest line unless the user scrolled up to read
- **And** long file names are shortened with "..." in the middle (full name on hover) and never
  push the panes around, at any window size down to the minimum

**Scenario: Partial progress survives a failure**
- **Given** a file is being transcribed
- **When** transcription fails or the app quits midway
- **Then** the lines produced so far are already saved next to the audio file
- **And** a failed file shows the error in red in the right pane, plus "The partial transcript
  was saved." when there was any text

**Scenario: The saved transcript**
- **Given** a file finished transcribing
- **When** the user looks next to the audio file
- **Then** there is a file with the same name and the chosen extension (.txt or .srt)
- **And** when another audio or video file with the same name sits in that folder (e.g.
  `talk.mp3` and `talk.mp4`), the original extension is kept (`talk.mp3.txt`) so transcripts
  never overwrite each other
- **And** the right pane says "Saved as <file name>" (full path on hover) and offers "Copy",
  "Open" and "Show in Finder"; "Copy" briefly changes to "Copied"

**Scenario: A file that can't be read**
- **Given** a queued file is corrupt or is a video without an audio track
- **When** its turn comes
- **Then** it fails with a one-line reason such as "'clip.mp4' has no audio track to transcribe."
  or "'x.wma' couldn't be read as audio (...)."
- **And** the files after it are transcribed normally

**Scenario: Managing the list**
- **Given** files are in the list
- **When** the user right-clicks a row
- **Then** they can "Open Transcript" (when saved), "Show in Finder", "Try Again" (failed files) and
  "Remove from List" (any file that is not currently running)
- **And** a "Clear finished" button above the list (and File > Clear Finished) removes all done
  and failed rows once any exist

**Scenario: Quitting while transcribing**
- **Given** a file is being transcribed
- **When** the user closes the window
- **Then** a dialog "Quit while transcribing?" explains only the part transcribed so far is saved
- **And** "Cancel" (the default) keeps working; "Yes" quits

## Models

**Scenario: Choosing a model**
- **Given** the app is open
- **When** the user opens the model menu
- **Then** they see "Parakeet v3 - Fast multilingual, Apple Silicon (600MB)" (the default), then
  Whisper "XLarge - Even more accurate, slowest (3GB)", "Large - More accurate, slower (1.5GB)",
  "Base - Balance between accuracy and speed (500MB)" and "Small - Terrible, but fast (140MB)"
- **And** once closed, the menu shows just the short name ("Parakeet v3", "Whisper XLarge",
  "Whisper Large", "Whisper Base", "Whisper Small")
- **And** the first run of a model downloads it once and then works offline

**Scenario: Choosing the language**
- **Given** the app is open
- **When** the user opens the language menu
- **Then** they can pick "Auto-detect language" (default), English, Ukrainian, Swedish or Russian
- **And** Parakeet ignores the choice and detects the language itself

**Scenario: Parakeet on a Mac that can't run it**
- **Given** the Mac has an Intel processor
- **When** the user opens the model menu
- **Then** Parakeet is greyed out with "- not supported on this Mac" and a tooltip explaining it
  needs an Apple Silicon Mac (M1 or newer)
- **And** a Whisper model is selected by default instead

**Scenario: Parakeet is set up automatically**
- **Given** the packaged app, which already includes Parakeet
- **Or** a source checkout on Apple Silicon where Parakeet's package is missing
- **When** the user transcribes with Parakeet
- **Then** it just works; for a source checkout the row first shows "Installing Parakeet
  (one-time, ~1 min)..." and installs it automatically
- **And** the user is never told to run an install command; if setup fails, the file fails with
  a message suggesting a Whisper model

## Speaker separation

**Scenario: Separating speakers**
- **Given** "Separate speakers" is ticked and the speaker count is set (default 2; "Auto" guesses)
- **When** a file finishes transcribing
- **Then** a second step "Separating speakers..." runs with its own progress
- **And** each line is tagged with a speaker number starting at 0, and a line where the voice
  changes mid-sentence is split at the word where it changes (Parakeet only)
- **And** voices that sound identical are not split apart even if the count allows it
- **And** the first use downloads two small models once, with no account needed

## Output format

**Scenario: Customizing the output**
- **Given** the user opens Settings (Cmd+, or the "Settings" button) on the "Output" tab
- **When** they change any option
- **Then** it applies immediately to files that start afterwards and is remembered between launches
- **And** a PREVIEW box shows two sample lines in the chosen format
- **And** the options are: "Save as" Plain text (.txt, default) or Subtitles (.srt); "Timestamps"
  Start and end (default), Start only or None; "Precision" Milliseconds (00:01:05.250, default)
  or Seconds (00:01:05); "Label speakers (when Separate speakers is on)", on by default

**Scenario: Plain text format**
- **Given** "Save as" is Plain text
- **When** a transcript is saved
- **Then** each line reads `[00:00:01.200 --> 00:00:04.850]: text` with start and end,
  `[00:00:01.200]: text` with start only, or just the text with no timestamps
- **And** with speaker labels the tag follows the timestamp, e.g. `[00:00:01.200] [Speaker 0]:
  text`, or `[Speaker 0]: text` without timestamps
- **And** Seconds precision drops the milliseconds (`00:00:01`)

**Scenario: Subtitle format**
- **Given** "Save as" is Subtitles (.srt)
- **When** a transcript is saved
- **Then** it is a standard numbered SRT file with `00:00:01,200 --> 00:00:04,850` ranges
- **And** the Timestamps and Precision options are disabled with the note "Subtitles always
  include start and end times."
- **And** speaker labels, when on, prefix the text as `[Speaker 0] text`

**Scenario: Finished transcripts keep their format**
- **Given** a file finished and was saved in one format
- **When** the user changes the output options afterwards
- **Then** that file's transcript in the right pane still shows the format it was saved in

## Updates

**Scenario: Checking for updates**
- **Given** the user opens Settings > "Updates" (or the app menu "Check for Updates...")
- **When** they click "Check for updates"
- **Then** the app asks GitHub for the latest release of the repository in "Update source"
- **And** it shows "You're up to date (<version>)." or "Version <x> is available (<size> MB)."
  with an "Update and restart" button
- **And** the app never checks on its own

**Scenario: Installing an update**
- **Given** a newer release is available and nothing is being transcribed
- **When** the user clicks "Update and restart"
- **Then** a progress bar and "Downloading... <percent>% (<done> of <total> MB)" appear
- **And** after "Installing and restarting..." the app quits, replaces itself with the new
  version in the same place and opens again
- **And** if the swap fails, the previous version is put back
- **And** closing the Settings window during the download cancels it

**Scenario: Update blocked**
- **Given** a newer release is available
- **When** a file is being transcribed, the app runs from source, or its folder is not writable
- **Then** the app doesn't update and says why: "Finish transcribing first, then update.", to use
  git pull, or to move the app to Applications or the user folder

**Scenario: Changing the update source**
- **Given** the user is on Settings > "Updates"
- **When** they enter another repository as owner/repo or a GitHub URL
- **Then** future checks use it, and "Reset" restores `olchyk98/UIWhisperCPP`
- **And** an invalid entry shows "'<text>' isn't a GitHub repository. Use the form owner/repo..."
- **And** a repository without releases shows "No releases found for that repository.", a
  release without an app download says so, and no internet shows "Couldn't reach GitHub. Check
  your internet connection."
