# VHS tape — a scripted, reproducible README demo.
#   1. Install VHS:  brew install vhs   (or: https://github.com/charmbracelet/vhs)
#   2. Make sure `resume` is set up (run it once, do onboarding + bootstrap).
#   3. Record:       vhs docs/demo/demo.tape       → writes docs/demo/demo.gif
#
# NOTE: the Sleep values below must cover your model's real latency. Do ONE dry run,
# watch where the recording types before the agent has answered, and bump those
# Sleeps up. Use a fast model (e.g. gemini-flash / haiku) for a snappier demo.
# A single-line target is used on purpose — a multi-line paste would submit on Enter.

Output docs/demo/demo.gif

Set Shell "bash"
Set FontSize 15
Set Width 1100
Set Height 720
Set Padding 22
Set TypingSpeed 55ms
Set Theme "Catppuccin Mocha"

Type "resume"       Sleep 400ms  Enter
Sleep 5s            # greeting

Type "Senior GenAI Engineer at a fintech: agentic LLM systems, RAG, MLOps with feature stores/CI-CD/MLflow, vector DBs; Kubernetes and Go/Java are nice-to-have"
Sleep 600ms  Enter
Sleep 14s           # assess + gap analysis (tune to your model)

Type "yes — shipped a RAG pipeline with a feature store and CI/CD at my last role"
Sleep 500ms  Enter
Sleep 3s

Type "skip"         Sleep 400ms  Enter   # the metrics ask
Sleep 16s           # fill / build (tune)

Type "2"            Sleep 400ms  Enter   # open the first role
Sleep 3s
Type "tighten 1"    Sleep 400ms  Enter   # one scoped edit
Sleep 9s
Type "back"         Sleep 400ms  Enter
Sleep 2s
Type "done"         Sleep 400ms  Enter   # generate
Sleep 10s           # finalize + render PDF/DOCX
Sleep 2s
