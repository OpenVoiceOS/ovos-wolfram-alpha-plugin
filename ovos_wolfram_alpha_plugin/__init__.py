# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import tempfile
from os.path import join, isfile
from typing import Optional, Tuple, Dict, Any, List

import requests
from ovos_config import Configuration
from ovos_plugin_manager.templates.language import LanguageTranslator
from ovos_plugin_manager.templates.agent_tools import AgentTool, ToolBox, ToolOutput, ToolArguments
from ovos_plugin_manager.templates.agents import RetrievalEngine
from ovos_utils.text_utils import rm_parentheses
from pydantic import Field
from ovos_utils.log import LOG
from ovos_plugin_manager.language import load_tx_plugin


class WolframAlphaApi:
    def __init__(self, key: str):
        self.key = key or "Y7R353-9HQAAL8KKA"

    @staticmethod
    def _request(url: str, params: dict) -> requests.Response:
        """GET *url*; a failed request raises with the query string left out.

        requests writes the full URL into the text of a connection error, and
        the query string carries the appid and the user's location.
        """
        try:
            return requests.get(url, params=params)
        except requests.RequestException as e:
            error = type(e)(f"{type(e).__name__} for {url}")
        # raised outside the except block, so the original error is not chained
        raise error

    @staticmethod
    def _get_lat_lon(**kwargs):
        lat = kwargs.get("latitude") or kwargs.get("lat")
        lon = kwargs.get("longitude") or kwargs.get("lon") or kwargs.get("lng")
        if not lat or not lon:
            cfg = Configuration().get("location", {}).get("coordinate", {})
            lat = cfg.get("latitude")
            lon = cfg.get("longitude")
        return lat, lon

    def spoken(self, query, units="metric", lat_lon=None, optional_params=None):
        optional_params = optional_params or {}
        if not lat_lon:
            lat_lon = self._get_lat_lon(**optional_params)
        params = {'i': query,
                  "geolocation": "{},{}".format(*lat_lon),
                  'units': units,
                  "appid": self.key,
                  **optional_params}
        url = 'https://api.wolframalpha.com/v1/spoken'
        response = self._request(url, params)
        # 501 is the API's "no answer" reply; its body says so in plain words.
        # The request URL carries the appid and the user's location, so the
        # error names only the status.
        if not response.ok and response.status_code != 501:
            raise requests.HTTPError(f"{response.status_code} {response.reason}", response=response)
        return response.text

    def simple(self, query, units="metric", lat_lon=None, optional_params=None):
        optional_params = optional_params or {}
        if not lat_lon:
            lat_lon = self._get_lat_lon(**optional_params)
        params = {'i': query,
                  "geolocation": "{},{}".format(*lat_lon),
                  'units': units,
                  "appid": self.key,
                  **optional_params}
        url = 'https://api.wolframalpha.com/v1/simple'
        return self._request(url, params).text

    def query_recognizer(self, query, units="metric", lat_lon=None, optional_params=None):
        optional_params = optional_params or {}
        params = {'i': query,
                  "appid": self.key,
                  "output": "json",
                  "mode": "Voice",
                  **optional_params}
        url = 'https://www.wolframalpha.com/queryrecognizer/query.jsp'
        # TODO - {"status":401,"message":"Not permitted"}
        # what am i doin wrong? https://products.wolframalpha.com/query-recognizer/documentation
        return self._request(url, params).json()

    def llm(self, query, units="metric", lat_lon=None, optional_params=None):
        optional_params = optional_params or {}
        if not lat_lon:
            lat_lon = self._get_lat_lon(**optional_params)
        params = {'input': query,
                  "geolocation": "{},{}".format(*lat_lon),
                  'units': units,
                  "appid": self.key,
                  **optional_params}
        url = 'https://api.wolframalpha.com/v1/llm-api'
        response = self._request(url, params)
        # 501 is the API's "no answer" reply; its body says so in plain words.
        # The request URL carries the appid and the user's location, so the
        # error names only the status.
        if not response.ok and response.status_code != 501:
            raise requests.HTTPError(f"{response.status_code} {response.reason}", response=response)
        return response.text

    def full_results(self, query, units="metric", lat_lon=None, optional_params=None):
        """Wrapper for the WolframAlpha Full Results v2 API.
        https://products.wolframalpha.com/api/documentation/
        Pods of interest
        - Input interpretation - Wolfram's determination of what is being asked about.
        - Name - primary name of
        """
        optional_params = optional_params or {}
        if not lat_lon:
            lat_lon = self._get_lat_lon(**optional_params)
        params = {'input': query,
                  "units": units,
                  "mode": "Default",
                  "format": "image,plaintext",
                  "geolocation": "{},{}".format(*lat_lon),
                  "output": "json",
                  "appid": self.key,
                  **optional_params}
        url = 'https://api.wolframalpha.com/v2/query'
        data = self._request(url, params)
        return data.json()

    def get_image(self, query: str, units: Optional[str] = None):
        """Return path to a image result for the query."""
        units = units or Configuration().get("system_unit", "metric")
        url = 'http://api.wolframalpha.com/v1/simple'
        params = {"appid": self.key,
                  "i": query,
                  # "background": "F5F5F5",
                  "layout": "labelbar",
                  "units": units}
        path = join(tempfile.gettempdir(), query.replace(" ", "_") + ".gif")
        if not isfile(path):
            image = self._request(url, params).content
            with open(path, "wb") as f:
                f.write(image)
        return path


class WolframAlphaRetrievalEngine(RetrievalEngine):
    def __init__(self, config=None, translator: Optional[LanguageTranslator] = None):
        super().__init__(config=config)
        self.api = WolframAlphaApi(key=self.config.get("appid") or "Y7R353-9HQAAL8KKA")
        self.translator: Optional[LanguageTranslator] = translator
        if not translator:
            self._load_translator()

    def _load_translator(self):
        lang_cfg = Configuration().get("language", {})
        plug_id = self.config.get("translate_plugin") or lang_cfg.get("translation_module", "ovos-translate-plugin-server")
        clazz = load_tx_plugin(plug_id)
        if clazz is None:
            LOG.error(f"Translation plugin not loaded '{plug_id}': only english queries will be answered")
        else:
            self.translator = clazz(config=lang_cfg.get(plug_id, {}))
            LOG.debug(f"Loaded translation plugin: '{plug_id}'")

    def query(self, query: str, lang: Optional[str] = None, k: int = 3) -> List[Tuple[str, float]]:
        """
        Searches Wolfram Alpha for a spoken answer.

        Args:
            query: The search string.
            lang: BCP-47 language code.
            k: Maximum number of results (unused; Wolfram returns one answer).

        Returns:
            List of (content, score) tuples, or empty list if no answer.
        """
        units = self.config.get("units") or Configuration().get("system_unit", "metric")
        try:
            answer = self.get_spoken_answer(query, lang, units)
        except requests.RequestException as e:
            # str(e) can hold the request URL, which carries the appid
            status = f" {e.response.status_code}" if e.response is not None else ""
            LOG.error(f"Wolfram Alpha request failed: {type(e).__name__}{status}")
            return []
        if answer:
            # TODO - use query recognizer to get a confidence level
            return [(answer, 0.9)]
        return []

    ################################
    # helpers to parse api results
    ################################
    def get_image(self, query: str,
                  lang: Optional[str] = None,
                  units: Optional[str] = None):
        """Return path to a cached image result for the query."""
        if lang != "en":
            query = self.translator.translate(query, target="en", source=lang)
        units = units or Configuration().get("system_unit", "metric")
        return self.api.get_image(query, units=units)

    def get_spoken_answer(self, query: str,
                          lang: Optional[str] = None,
                          units: Optional[str] = None):
        """Return a single natural-language sentence answering the query."""
        lang = lang.split("-")[0].lower()
        if lang != "en":
            query = self.translator.translate(query, target="en", source=lang)
        units = units or Configuration().get("system_unit", "metric")
        answer = self.api.spoken(query, units=units)
        bad_answers = ["no spoken result available",
                       "wolfram alpha did not understand your input"]
        if answer.lower().strip() in bad_answers:
            return None
        if lang != "en":
            answer = self.translator.translate(answer, target=lang, source="en")
        return answer

    def get_expanded_answer(self, query,
                            lang: Optional[str] = None,
                            units: Optional[str] = None):
        """Return a list of structured result pods from the Full Results API."""
        if lang != "en":
            query = self.translator.translate(query, target="en", source=lang)
        data = self.api.full_results(query, units=units)
        skip = ['Input interpretation', 'Interpretation',
                'Result', 'Value', 'Image']
        steps = []

        for pod in data['queryresult'].get('pods', []):
            title = pod["title"]
            if title in skip:
                continue

            for sub in pod["subpods"]:
                subpod = {"title": title}
                summary = sub["img"]["alt"]
                subtitle = sub.get("title") or sub["img"]["title"]
                if subtitle and subtitle != summary:
                    subpod["title"] = subtitle

                if summary == title:
                    # it's an image result
                    subpod["img"] = sub["img"]["src"]
                elif summary.startswith("(") and summary.endswith(")"):
                    continue
                else:
                    subpod["summary"] = summary
                steps.append(subpod)

        # do any extra processing here
        prev = ""
        for idx, step in enumerate(steps):
            # merge steps
            if step["title"] == prev:
                summary = steps[idx - 1]["summary"] + "\n" + step["summary"]
                steps[idx]["summary"] = summary
                steps[idx]["img"] = step.get("img") or steps[idx - 1].get("img")
                steps[idx - 1] = None
            elif step.get("summary") and step["title"]:
                # inject title in speech, eg we do not want wolfram to just read family names without context
                steps[idx]["summary"] = step["title"] + ".\n" + step["summary"]

            # normalize summary
            if step.get("summary"):
                steps[idx]["summary"] = self.make_speakable(steps[idx]["summary"])

            if lang != "en":
                steps[idx]["title"] = self.translator.translate(steps[idx]["title"], target=lang, source="en")
                if step.get("summary"):
                    steps[idx]["summary"] = self.translator.translate(steps[idx]["summary"], target=lang, source="en")

            prev = step["title"]
        return [s for s in steps if s]

    @staticmethod
    def make_speakable(summary: str):
        """Normalise a Wolfram result string for text-to-speech."""
        # let's remove unwanted data from parantheses
        #  - many results have (human: XX unit) ref values, remove them
        if "(human: " in summary:
            splits = summary.split("(human: ")
            for idx, s in enumerate(splits):
                splits[idx] = ")".join(s.split(")")[1:])
            summary = " ".join(splits)

        # remove duplicated units in text
        # TODO probably there's a lot more to add here....
        replaces = {
            "cm (centimeters)": "centimeters",
            "cm³ (cubic centimeters)": "cubic centimeters",
            "cm² (square centimeters)": "square centimeters",
            "mm (millimeters)": "millimeters",
            "mm² (square millimeters)": "square millimeters",
            "mm³ (cubic millimeters)": "cubic millimeters",
            "kg (kilograms)": "kilograms",
            "kHz (kilohertz)": "kilohertz",
            "ns (nanoseconds)": "nanoseconds",
            "µs (microseconds)": "microseconds",
            "m/s (meters per second)": "meters per second",
            "km/s (kilometers per second)": "kilometers per second",
            "mi/s (miles per second)": "miles per second",
            "mph (miles per hour)": "miles per hour",
            "ª (degrees)": " degrees"
        }
        for k, v in replaces.items():
            summary = summary.replace(k, v)

        # replace units, only if they are individual words
        units = {
            "cm": "centimeters",
            "cm³": "cubic centimeters",
            "cm²": "square centimeters",
            "mm": "millimeters",
            "mm²": "square millimeters",
            "mm³": "cubic millimeters",
            "kg": "kilograms",
            "kHz": "kilohertz",
            "ns": "nanoseconds",
            "µs": "microseconds",
            "m/s": "meters per second",
            "km/s": "kilometers per second",
            "mi/s": "miles per second",
            "mph": "miles per hour"
        }
        words = [w if w not in units else units[w]
                 for w in summary.split(" ")]
        summary = " ".join(words)
        return rm_parentheses(summary)


class SearchWolframAlphaArgs(ToolArguments):
    query: str = Field(..., description="The natural language or mathematical query to look up on Wolfram Alpha. Convert conversational phrasing to concise keywords (e.g. 'France population' not 'how many people live in France'). Always send in English.")
    units: str = Field("metric", description="Unit system for the answer: 'metric' or 'imperial'.")


class SearchWolframAlphaOutput(ToolOutput):
    result: str = Field(..., description="The LLM-optimised Wolfram Alpha answer for the query.")


class WolframAlphaToolbox(ToolBox):
    toolbox_id = "ovos-wolfram-alpha-tools"

    def __init__(self, config: Optional[Dict[str, Any]] = None, bus: Optional[Any] = None) -> None:
        """
        Initialise the toolbox.

        Args:
            config: Plugin configuration dict.
            bus: The OVOS Messagebus client instance, passed through to ToolBox.
        """
        self.config = config or {}
        self.api = WolframAlphaApi(key=self.config.get("appid") or "Y7R353-9HQAAL8KKA")
        super().__init__(toolbox_id=self.toolbox_id, config=config, bus=bus)

    def search_wolfram(self, args: SearchWolframAlphaArgs) -> SearchWolframAlphaOutput:
        """Query Wolfram Alpha and return the LLM-optimised answer."""
        return SearchWolframAlphaOutput(
            result=self.api.llm(query=args.query, units=args.units)
        )

    def discover_tools(self) -> List[AgentTool]:
        """
        Abstract method to be implemented by concrete ToolBox plugins.

        This method must define and return the list of AgentTools provided by this plugin.
        The implementation should be idempotent (safe to call multiple times).

        Returns:
            A list of instantiated AgentTool objects.
        """
        return [
            AgentTool(
                name="search_wolfram_alpha",
                description=(
                    "Query Wolfram Alpha for factual answers: maths, science, unit conversions, "
                    "geography, history, and more. Always send queries in English as concise keywords."
                ),
                argument_schema=SearchWolframAlphaArgs,
                output_schema=SearchWolframAlphaOutput,
                tool_call=self.search_wolfram,
            )
        ]


if __name__ == "__main__":
    s = WolframAlphaRetrievalEngine()
    print(s.api.spoken("Who is the author of The Fault in our Stars"))
