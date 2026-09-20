#!/usr/bin/env python3
"""Entry point: python crawl.py <booking.com property URL>"""

import sys

from booking_crawler.cli import main

if __name__ == "__main__":
    sys.exit(main())
