# ovos-wolfram-alpha-plugin

[![PyPI](https://img.shields.io/pypi/v/ovos-wolfram-alpha-plugin)](https://pypi.org/project/ovos-wolfram-alpha-plugin/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue)](https://www.python.org/)

[Wolfram Alpha](https://www.wolframalpha.com/) integration for [OpenVoiceOS](https://openvoiceos.org). Provides a **retrieval engine** for RAG pipelines and an **agent toolbox** for tool-using agents, both as standard OPM plugins.

Wolfram Alpha excels at questions with a single definitive answer: maths, unit conversions, scientific constants, chemical properties, astronomy, nutrition, geography, and historical dates. It is not a search engine. It computes answers from curated data.

An [API key](https://products.wolframalpha.com/api/) is required. Set it as `appid` in the plugin config. The plugin has no built-in key: without an `appid` it logs an error and returns no answer.

---

## Installation

```bash
pip install ovos-wolfram-alpha-plugin
```

---

## OPM Entry Points

| Entry point | Class | Use case |
|---|---|---|
| `opm.agents.retrieval`, `ovos-wolfram-alpha-plugin` | `WolframAlphaRetrievalEngine` | RAG, returns `(answer, score)` tuples |
| `opm.agents.toolbox`, `ovos-wolfram-alpha-tools` | `WolframAlphaToolbox` | Agent tool use, exposes `search_wolfram_alpha` |

---

## Retrieval Engine

`WolframAlphaRetrievalEngine` implements the `RetrievalEngine` OPM interface. It calls the Wolfram Alpha spoken-answer endpoint and handles non-English queries by translating them to English before the request and back after.

```python
from ovos_wolfram_alpha_plugin import WolframAlphaRetrievalEngine

engine = WolframAlphaRetrievalEngine(config={"appid": "YOUR-KEY"})

# Maths & conversions
engine.get_spoken_answer("integral of x^2 sin(x)", lang="en")
# "x^2 (-cos(x)) + 2 x sin(x) + 2 cos(x) + constant"

engine.get_spoken_answer("100 miles in kilometers", lang="en")
# "160.934 kilometers"

engine.get_spoken_answer("1000 USD in EUR", lang="en")
# "approximately 923 euros"  (live rate)

# Science & constants
engine.get_spoken_answer("speed of light", lang="en")
# "about 2.998 × 10^8 meters per second"

engine.get_spoken_answer("boiling point of ethanol", lang="en")
# "78.37 degrees Celsius"

engine.get_spoken_answer("distance from Earth to Mars", lang="en")
# "currently about 1.69 AU"  (live ephemeris)

# Factual lookups
engine.get_spoken_answer("population of Brazil", lang="en")
# "approximately 215.3 million people"

engine.get_spoken_answer("calories in 100g of almonds", lang="en")
# "579 kilocalories"

engine.get_spoken_answer("when was the Eiffel Tower built", lang="en")
# "construction was from January 28, 1887 to March 31, 1889"

# Non-English, translated automatically
engine.get_spoken_answer("massa do Sol", lang="pt")
# "aproximadamente 1,989 × 10^30 kg"

# Image result, returns a local file path to a Wolfram visual
engine.get_image("benzene molecular structure", lang="en")

# Full structured pod results, list of {"title", "summary"} dicts
for pod in engine.get_expanded_answer("Neptune", lang="en"):
    print(pod["title"], "-", pod.get("summary", pod.get("img")))
# "Orbital period - 164.8 years"
# "Surface gravity - 11.15 m/s²"
# ...

# RAG interface: List[Tuple[str, float]]  (answer, score)
results = engine.query("half-life of carbon-14", lang="en")
# [("5730 years", 0.9)]
```

### Translation

Non-English queries require a translation plugin. Configure it by passing `translate_plugin` in the config:

```python
engine = WolframAlphaRetrievalEngine(config={
    "appid": "YOUR-KEY",
    "translate_plugin": "ovos-translate-plugin-server",
})
```

If no translation plugin is available, only English queries are answered.

---

## Agent Toolbox

`WolframAlphaToolbox` exposes a single `search_wolfram_alpha` tool that any OPM-compatible agent loop (e.g. [ovos-agentic-loop](https://github.com/OpenVoiceOS/ovos-agentic-loop)) can discover and call. The tool uses the LLM-optimised Wolfram endpoint, which returns a more structured answer than the spoken endpoint.

### Persona JSON

Reference the toolbox by its entry point name inside any agentic persona. Pass a `system_prompt` to the brain plugin so the LLM knows how to query Wolfram correctly:

```json
{
  "name": "Wolfram Alpha",
  "solvers": ["ovos-react-loop"],
  "ovos-react-loop": {
    "brain": "ovos-chat-openai-plugin",
    "toolboxes": ["ovos-wolfram-alpha-tools"],
    "ovos-chat-openai-plugin": {
      "api_url": "http://localhost:11434/v1/chat/completions",
      "system_prompt": "You have access to Wolfram Alpha. Use it for maths, science, unit conversions, and factual questions with a definite answer. Always send queries in English as concise keywords (e.g. 'France population', not 'how many people live in France'). Use the exponent notation 6*10^14, never 6e14. If the result is not relevant, retry with a more specific query rather than rephrasing."
    }
  }
}
```

> See the [official LLM API docs](https://products.wolframalpha.com/llm-api/documentation) for more tips on writing effective Wolfram system prompts.

### Direct usage

```python
from ovos_wolfram_alpha_plugin import WolframAlphaToolbox, SearchWolframAlphaArgs

tb = WolframAlphaToolbox(config={"appid": "YOUR-KEY"})

tools = tb.discover_tools()
# [AgentTool(name="search_wolfram_alpha", ...)]

output = tb.search_wolfram(SearchWolframAlphaArgs(query="France population", units="metric"))
print(output.result)
```

---

## Related projects

- [OpenVoiceOS/ovos-skill-wolfie](https://github.com/OpenVoiceOS/ovos-skill-wolfie) — a voice skill that wires `WolframAlphaRetrievalEngine` into the OVOS intent, Common Query, and fallback pipelines.
- [OpenVoiceOS/ovos-agentic-loop](https://github.com/OpenVoiceOS/ovos-agentic-loop) — an agent loop that can discover and call the `WolframAlphaToolbox` tool.

---

## Docker

This repo publishes `ghcr.io/openvoiceos/ovos-wolfram-alpha-plugin`, a
standalone `ovos-persona-server` that serves one persona, `WolframBot`,
backed by the `WolframAlphaRetrievalEngine` in this plugin. Every query hits
`api.wolframalpha.com` and needs an `appid`. The plugin has no built-in
`appid`. If `WOLFRAM_APPID` is not set, the container starts and logs that no
`appid` is configured, and every query returns no answer. Get your own free
`appid` from [the Wolfram developer portal](https://developer.wolframalpha.com/)
and pass it as `WOLFRAM_APPID`:

```bash
docker run -p 8392:8337 -e WOLFRAM_APPID=your-appid-here \
    ghcr.io/openvoiceos/ovos-wolfram-alpha-plugin:dev
```

```bash
curl http://localhost:8392/v1/models
curl http://localhost:8392/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model": "WolframBot", "messages": [{"role": "user", "content": "distance from earth to the moon"}]}'
```

A compose snippet:

```yaml
services:
  ovos-wolfram-persona:
    image: ghcr.io/openvoiceos/ovos-wolfram-alpha-plugin:dev
    ports:
      - "8392:8337"
    environment:
      - WOLFRAM_APPID=your-appid-here
    restart: unless-stopped
```

The image builds on every pull request touching `Dockerfile`,
`entrypoint.sh`, `.dockerignore`, `pyproject.toml`, or the docker workflow
itself (build only, no push), and publishes on pushes to `master` (`latest`),
`dev` (`dev`), and version tags. See the
[`docker` workflow](.github/workflows/docker.yml).

## License

Apache 2.0. See [LICENSE](LICENSE).
