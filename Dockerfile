# ovos-persona-server serving a persona backed by this plugin's retrieval
# engine (opm.agents.retrieval, WolframAlphaRetrievalEngine). Every request
# hits api.wolframalpha.com and needs an "appid" key. The plugin has no
# built-in key: entrypoint.sh reads WOLFRAM_APPID at start and writes it into
# the persona config. Without it, the plugin logs that no appid is configured
# and every query returns no answer. See README.md.
FROM python:3.15-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Prerelease floor: ovos-persona needs a version new enough to resolve a
# retrieval-engine entry point as a persona handler.
RUN pip install --no-cache-dir "ovos-persona>=0.9.0a9"

# This plugin is installed from PyPI rather than the local checkout, so the
# image always matches whatever release is public -- there is no local
# source dependency to build from here.
RUN pip install --no-cache-dir ovos-wolfram-alpha-plugin

# [mcp] mounts the MCP tool endpoint. From 0.17.0a1 that mount is opt-in --
# installing the extra alone no longer flips it on, so --mcp below is
# required, matching ovos-plugin-linguonnx's image.
RUN pip install --no-cache-dir "ovos-persona-server[mcp]>=0.17.0a1"

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8337

ENTRYPOINT ["/entrypoint.sh"]
