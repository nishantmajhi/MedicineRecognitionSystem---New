import logging
from typing import Dict, Optional

from src import http_client
from src.synonym_resolver import SynonymResolver

logger = logging.getLogger("fda_client.py")


class FDAClient:
    """
    Queries the openFDA drug-label API for one generic name.

    openFDA indexes US generic names (USAN). Packaging sold in Nepal prints the
    INN, so the name is resolved before querying and the answer is renamed back
    afterwards -- a user who photographed a "Paracetamol" box should not be told
    about "acetaminophen".

    Resolution used to be a hardcoded ``if name == "paracetamol"``. It is now
    delegated to SynonymResolver, which covers the whole INN/USAN vocabulary.
    """

    BASE_URL = 'https://api.fda.gov/drug/label.json?search=openfda.generic_name:'

    def __init__(self, timeout: int = 10, resolver: Optional[SynonymResolver] = None):
        self.timeout = timeout
        self.resolver = resolver if resolver is not None else SynonymResolver()

    def query(self, generic_name: str) -> Optional[Dict[str, str]]:
        try:
            logger.info(f"Querying FDA API for: {generic_name}")

            resolution = self.resolver.resolve(generic_name)
            query_name = resolution.resolved_name
            if resolution.changed:
                logger.info(
                    f"Resolved '{generic_name}' to '{query_name}' "
                    f"(via {resolution.source})"
                )

            # Percent-encode: generic names can contain spaces, e.g.
            # "dextromethorphan hydrobromide".
            resp = http_client.get(
                f'{self.BASE_URL}"{http_client.quote(query_name)}"',
                timeout=self.timeout,
            )

            if resp.status_code == 200:
                data = self._parse_response(resp.json(), query_name)
                # Report the drug under the name the user's package printed.
                if data and resolution.changed:
                    data = self._replace_generic_name(
                        data, query_name, resolution.source_name
                    )
                return data

            logger.warning(f"FDA API returned status code: {resp.status_code}")
            return None
        except http_client.RequestException as e:
            logger.error(f"FDA request failed: {str(e)}")
            return None
        except Exception as e:
            logger.exception(f"Exception in FDA query: {e}")
            return None

    def _parse_response(self, json_data: dict, generic_name: str) -> Optional[Dict[str, str]]:
        try:
            res = json_data['results'][0]
            return {
                'generic_name': generic_name,
                'purpose': res.get('purpose', [''])[0].lower(),
                'do_not_use': res.get('do_not_use', [''])[0].lower()
            }
        except Exception as e:
            logger.error(f"FDA response parse error: {e}")
            return None

    @staticmethod
    def _replace_generic_name(data: Dict[str, str], old: str, new: str) -> Dict[str, str]:
        """
        Swap one drug name for another throughout the response.

        Values arrive lowercased from _parse_response, so only the lowercase
        form can occur; the other cases are handled defensively in case that
        changes.
        """
        def replace_case(text: str) -> str:
            return (
                text.replace(old.lower(), new.lower())
                .replace(old.capitalize(), new.capitalize())
                .replace(old.upper(), new.upper())
            )

        return {k: replace_case(v) if isinstance(v, str) else v for k, v in data.items()}
