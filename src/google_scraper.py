from typing import List,Optional, Union, Dict
from botasaurus import bt
from .write_output import write_output

from .search import FAILED_DUE_TO_CREDITS_EXHAUSTED, FAILED_DUE_TO_NO_KEY,FAILED_DUE_TO_NOT_SUBSCRIBED, FAILED_DUE_TO_UNKNOWN_ERROR, search


def clean_data(social_details):
    success, credits_exhausted, not_subscribed, unknown_error, no_key = [], [], [], [], []

    for detail in social_details:
        if detail.get("error") is None:
            success.append(detail)
        elif detail["error"] == FAILED_DUE_TO_CREDITS_EXHAUSTED:
            credits_exhausted.append(detail)
        elif detail["error"] == FAILED_DUE_TO_NOT_SUBSCRIBED:
            not_subscribed.append(detail)
        elif detail["error"] == FAILED_DUE_TO_UNKNOWN_ERROR:
            unknown_error.append(detail)
        elif detail["error"] == FAILED_DUE_TO_NO_KEY:
            no_key.append(detail)

    return success, credits_exhausted, not_subscribed, unknown_error, no_key

def print_data_errors(credits_exhausted, not_subscribed, unknown_error, no_key):
    
    if credits_exhausted:
        name = "queries" if len(credits_exhausted) > 1 else "query"
        print(f"Could not get data for {len(credits_exhausted)} {name} due to credit exhaustion. Please consider upgrading your plan by visiting https://rapidapi.com/Chetan11dev/api/google-scraper/pricing to continue scraping data.")

    if not_subscribed:
        name = "queries" if len(not_subscribed) > 1 else "query"
        print(f"Could not get data for {len(not_subscribed)} {name} as you are not subscribed to Google Scraper API. Please subscribe to a free plan by visiting https://rapidapi.com/Chetan11dev/api/google-scraper/pricing")

    if unknown_error:
        name = "queries" if len(unknown_error) > 1 else "query"
        print(f"Could not get data for {len(unknown_error)} {name} due to Unknown Error.")

    if no_key:
        name = "queries" if len(no_key) > 1 else "query"
        print(f"Could not get data for {len(no_key)} {name} as you are not subscribed to Google Scraper API. Please subscribe to a free plan by visiting https://rapidapi.com/Chetan11dev/api/google-scraper/pricing")

      
class Google:
    
    @staticmethod
    def search(query:  Union[str, List[str]],  max: Optional[int] = None, key: Optional[str] =None,  use_cache: bool = True) -> Dict:
        """
        Function to scrape data from Google.
        :param use_cache: Boolean indicating whether to use cached data.
        :return: List of dictionaries with the scraped data.
        """
        cache = use_cache
        if isinstance(query, str):
            query = [query]  

        query = [{"query":query_query, "max": max} for query_query in query]
        result = []
        for item in query:
            # TODO: max fixes
            data = item
            metadata = {"key": key}
            
            result_item = search(data, cache=cache, metadata=metadata)
            
            success, credits_exhausted, not_subscribed, unknown_error, no_key = clean_data([result_item])
            print_data_errors(credits_exhausted, not_subscribed, unknown_error, no_key)

            if success:
                data = result_item.get('data')
                if not data:
                    data = {}

                result_item = data.get('results', [])
                result.extend(result_item)
                write_output(item['query'], result_item,None)

        if result:
            # bt.write_json(result, "result")
            write_output('_all',result, None, lambda x:x)
        
        search.close()

        return result