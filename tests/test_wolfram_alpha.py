"""
Unit tests for ovos-wolfram-alpha-plugin.

All network calls and OVOS config are mocked — no API key or daemon required.
"""
import unittest
from unittest.mock import MagicMock, patch, mock_open

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus

from ovos_wolfram_alpha_plugin import (
    WolframAlphaApi,
    WolframAlphaRetrievalEngine,
    WolframAlphaToolbox,
    SearchWolframAlphaArgs,
    SearchWolframAlphaOutput,
)


# ---------------------------------------------------------------------------
# WolframAlphaApi
# ---------------------------------------------------------------------------

class TestWolframAlphaApi(unittest.TestCase):

    def setUp(self):
        self.api = WolframAlphaApi(key="TEST-KEY")

    def test_key_stored(self):
        self.assertEqual(self.api.key, "TEST-KEY")

    def test_default_key_used_when_falsy(self):
        api = WolframAlphaApi(key=None)
        self.assertEqual(api.key, "Y7R353-9HQAAL8KKA")

    def _mock_get(self, text="answer"):
        m = MagicMock()
        m.text = text
        m.content = b"imagedata"
        return m

    def test_spoken_returns_text(self):
        with patch("ovos_wolfram_alpha_plugin.requests.get") as mock_get:
            mock_get.return_value = self._mock_get("The speed of light is 3×10^8 m/s.")
            result = self.api.spoken("speed of light", lat_lon=(0, 0))
        self.assertEqual(result, "The speed of light is 3×10^8 m/s.")

    def test_llm_returns_text(self):
        with patch("ovos_wolfram_alpha_plugin.requests.get") as mock_get:
            mock_get.return_value = self._mock_get("42")
            result = self.api.llm("meaning of life", lat_lon=(0, 0))
        self.assertEqual(result, "42")

    def test_full_results_returns_json(self):
        payload = {"queryresult": {"pods": []}}
        with patch("ovos_wolfram_alpha_plugin.requests.get") as mock_get:
            mock_get.return_value.json.return_value = payload
            result = self.api.full_results("mercury", lat_lon=(0, 0))
        self.assertEqual(result, payload)

    def test_get_image_writes_file_on_miss(self):
        with patch("ovos_wolfram_alpha_plugin.isfile", return_value=False), \
             patch("ovos_wolfram_alpha_plugin.requests.get") as mock_get, \
             patch("builtins.open", mock_open()) as mocked_open:
            mock_get.return_value.content = b"gif"
            path = self.api.get_image("mercury")
        self.assertTrue(path.endswith(".gif"))
        mocked_open.assert_called_once()

    def test_get_image_skips_download_on_hit(self):
        with patch("ovos_wolfram_alpha_plugin.isfile", return_value=True), \
             patch("ovos_wolfram_alpha_plugin.requests.get") as mock_get:
            self.api.get_image("mercury")
        mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# WolframAlphaRetrievalEngine
# ---------------------------------------------------------------------------

class TestWolframAlphaRetrievalEngine(unittest.TestCase):

    def _make_engine(self):
        with patch("ovos_wolfram_alpha_plugin.load_tx_plugin", return_value=None), \
             patch("ovos_wolfram_alpha_plugin.Configuration", return_value={}):
            return WolframAlphaRetrievalEngine(config={"appid": "TEST"})

    def test_query_returns_answer_and_score(self):
        engine = self._make_engine()
        engine.get_spoken_answer = MagicMock(return_value="Venus is the second planet.")
        with patch("ovos_wolfram_alpha_plugin.Configuration", return_value={}):
            result = engine.query("venus", lang="en")
        self.assertEqual(result, [("Venus is the second planet.", 0.9)])

    def test_query_returns_empty_when_no_answer(self):
        engine = self._make_engine()
        engine.get_spoken_answer = MagicMock(return_value=None)
        with patch("ovos_wolfram_alpha_plugin.Configuration", return_value={}):
            result = engine.query("xyzzy", lang="en")
        self.assertEqual(result, [])

    def test_get_spoken_answer_filters_bad_responses(self):
        engine = self._make_engine()
        engine.api = MagicMock()
        engine.api.spoken.return_value = "Wolfram Alpha did not understand your input"
        with patch("ovos_wolfram_alpha_plugin.Configuration", return_value={}):
            result = engine.get_spoken_answer("gibberish", lang="en")
        self.assertIsNone(result)

    def test_get_spoken_answer_returns_answer_for_english(self):
        engine = self._make_engine()
        engine.api = MagicMock()
        engine.api.spoken.return_value = "Mercury is the smallest planet."
        with patch("ovos_wolfram_alpha_plugin.Configuration", return_value={}):
            result = engine.get_spoken_answer("mercury", lang="en")
        self.assertEqual(result, "Mercury is the smallest planet.")

    def test_get_spoken_answer_translates_non_english(self):
        engine = self._make_engine()
        engine.translator = MagicMock()
        engine.translator.translate.side_effect = lambda q, target, source: f"[{target}]{q}"
        engine.api = MagicMock()
        engine.api.spoken.return_value = "Mercury is a planet."
        with patch("ovos_wolfram_alpha_plugin.Configuration", return_value={}):
            result = engine.get_spoken_answer("mercúrio", lang="pt")
        self.assertIn("pt", result)


# ---------------------------------------------------------------------------
# make_speakable
# ---------------------------------------------------------------------------

class TestMakeSpeakable(unittest.TestCase):

    def test_removes_human_parenthetical(self):
        text = "The height is 1.8 m (human: 5 ft 11 in)"
        result = WolframAlphaRetrievalEngine.make_speakable(text)
        self.assertNotIn("human:", result)

    def test_expands_unit_abbreviation(self):
        result = WolframAlphaRetrievalEngine.make_speakable("mass is 5 kg")
        self.assertIn("kilograms", result)

    def test_expands_cm_unit(self):
        result = WolframAlphaRetrievalEngine.make_speakable("height 180 cm")
        self.assertIn("centimeters", result)

    def test_noop_on_plain_text(self):
        text = "The answer is forty-two."
        result = WolframAlphaRetrievalEngine.make_speakable(text)
        self.assertEqual(result, text)


# ---------------------------------------------------------------------------
# WolframAlphaToolbox
# ---------------------------------------------------------------------------

class TestWolframAlphaToolbox(unittest.TestCase):

    def _make_toolbox(self):
        with patch("ovos_wolfram_alpha_plugin.WolframAlphaApi"):
            return WolframAlphaToolbox(config={"appid": "TEST"})

    def test_discover_tools_returns_one_tool(self):
        tb = self._make_toolbox()
        tools = tb.discover_tools()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].name, "search_wolfram_alpha")

    def test_search_wolfram_returns_output(self):
        tb = self._make_toolbox()
        tb.api = MagicMock()
        tb.api.llm.return_value = "42"
        args = SearchWolframAlphaArgs(query="meaning of life", units="metric")
        result = tb.search_wolfram(args)
        self.assertIsInstance(result, SearchWolframAlphaOutput)
        self.assertEqual(result.result, "42")

    def test_toolbox_id(self):
        self.assertEqual(WolframAlphaToolbox.toolbox_id, "ovos-wolfram-alpha-tools")

    def test_constructs_the_way_the_persona_server_loader_does(self):
        # ovos_persona_server.tools._load_toolboxes calls cls(config=cfg, bus=bus).
        # A plugin whose __init__ does not accept bus raises a TypeError there
        # and the loader only logs a warning, so the toolbox silently vanishes.
        with patch("ovos_wolfram_alpha_plugin.WolframAlphaApi"):
            cfg = {"appid": "TEST"}
            tb = WolframAlphaToolbox(config=cfg, bus=None)
        self.assertEqual(tb.config, cfg)
        self.assertTrue(tb.tools)

    def test_constructor_forwards_bus_so_bind_actually_runs(self):
        # Passing a bus to __init__ must reach ToolBox.bind(), so the toolbox
        # answers the OVOS-TOOLS-1 topics with real values: discover lists the
        # tools of this toolbox, and a call runs the tool and returns its result.
        bus = FakeBus()
        with patch("ovos_wolfram_alpha_plugin.WolframAlphaApi"):
            tb = WolframAlphaToolbox(config={"appid": "TEST"}, bus=bus)
        tb.api = MagicMock()
        tb.api.llm.return_value = "42"
        replies = []
        bus.on("ovos.persona.tools.discover.response", lambda m: replies.append(m.data))
        bus.on(f"ovos.persona.tools.{tb.toolbox_id}.call.response", lambda m: replies.append(m.data))

        bus.emit(Message("ovos.persona.tools.discover"))
        bus.emit(Message(f"ovos.persona.tools.{tb.toolbox_id}.call",
                         {"name": "search_wolfram_alpha",
                          "kwargs": {"query": "meaning of life", "units": "imperial"}}))

        discovered, called = replies
        self.assertEqual(discovered["toolbox_id"], "ovos-wolfram-alpha-tools")
        self.assertEqual([t["name"] for t in discovered["tools"]], ["search_wolfram_alpha"])
        self.assertEqual(called["result"]["result"], "42")
        tb.api.llm.assert_called_once_with(query="meaning of life", units="imperial")


# ---------------------------------------------------------------------------
# Plugin loading
# ---------------------------------------------------------------------------

class TestPluginLoading(unittest.TestCase):

    def test_imports(self):
        from ovos_wolfram_alpha_plugin import (
            WolframAlphaApi,
            WolframAlphaRetrievalEngine,
            WolframAlphaToolbox,
        )


if __name__ == "__main__":
    unittest.main()
