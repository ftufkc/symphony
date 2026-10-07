# Symphony

Symphony turns project work into isolated, autonomous implementation runs, allowing teams to manage
work instead of supervising coding agents.

[![Symphony demo video preview](.github/media/symphony-demo-poster.jpg)](https://player.vimeo.com/video/1186371009?h=5626e4b899)

_In this [demo video](https://player.vimeo.com/video/1186371009?h=5626e4b899), Symphony monitors a Linear board for work and spawns agents to handle the tasks. The agents complete the tasks and provide proof of work: CI status, PR review feedback, complexity analysis, and walkthrough videos. When accepted, the agents land the PR safely. Engineers do not need to supervise Codex; they can manage the work at a higher level._

> [!WARNING]
> Symphony is a low-key engineering preview for testing in trusted environments.

## Plane CE fork

This fork adds Plane Community Edition v1 work-items as a coding-task tracker while retaining the
upstream Elixir scheduler, execution timeout controls and fresh threads per worker. Existing tracker
implementations are retained. See [Plane setup](elixir/docs/plane.md)
and [the coding workflow](elixir/PLANE_WORKFLOW.md). Repository preparation and PR/MR policy remain
workflow-owned, so code can be hosted on GitLab or another Git service.
Plane execution faults pause in `AI Error`; `Human Review` is reserved for review and decisions.
Move a repaired task back to `AI Todo` to retry. Normal turn-budget exhaustion retains upstream continuation.

The earlier Python implementation is preserved with its exact commit provenance in
[archive/](archive/README.md). The original local repository remains separate.

## Running Symphony

### Requirements

Symphony works best in codebases that have adopted
[harness engineering](https://openai.com/index/harness-engineering/). Symphony is the next step --
moving from managing coding agents to managing work that needs to get done.

### Option 1. Make your own

Tell your favorite coding agent to build Symphony in a programming language of your choice:

> Implement Symphony according to the following spec:
> https://github.com/openai/symphony/blob/main/SPEC.md

### Option 2. Use our experimental reference implementation

Check out [elixir/README.md](elixir/README.md) for instructions on how to set up your environment
and run the Elixir-based Symphony implementation. You can also ask your favorite coding agent to
help with the setup:

> Set up Symphony for my repository based on
> https://github.com/openai/symphony/blob/main/elixir/README.md

---

## License

This project is licensed under the [Apache License 2.0](LICENSE).
