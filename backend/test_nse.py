from backend.nse_api import get_option_chain
import json

def test():
    print("Testing NIFTY...")
    res = get_option_chain("NIFTY")
    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    test()
