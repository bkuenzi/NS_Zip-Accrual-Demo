"""Autonomous accounting accrual agent.

Identifies uninvoiced spend across NetSuite and Zip, confirms it through vendor
communications or connected vendor APIs (Google Ads, Meta), maintains a live
accrual register, and writes journal entries back to NetSuite.
"""

__version__ = "0.1.0"
