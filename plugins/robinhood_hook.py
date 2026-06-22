import os
import robin_stocks.robinhood as r
import logging

logger = logging.getLogger(__name__)

class RobinhoodHook:
    """LEGACY — unofficial ``robin_stocks`` (reverse-engineered) Robinhood API.

    Order *execution* has moved to Robinhood's official Agentic Trading MCP
    (see ``energy_trader.brokers.robinhood_mcp.RobinhoodMCPBroker``), which is
    sanctioned, OAuth-based, and contained to an isolated funded account. This
    hook is retained only for ad-hoc read-only introspection against your main
    account; do not use it for automated trading.
    """
    
    def __init__(self):
        self.username = os.getenv('ROBINHOOD_USERNAME')
        self.password = os.getenv('ROBINHOOD_PASSWORD')
        self.mfa_code = os.getenv('ROBINHOOD_MFA_CODE')
        
    def login(self):
        if not self.username or not self.password:
            raise ValueError("Robinhood credentials not found in environment.")
            
        logger.info("Attempting to login to Robinhood...")
        try:
            # Login. If MFA is required, it will prompt or use the mfa_code if provided.
            # In a production automated setting, you might need to handle the MFA token differently.
            r.login(self.username, self.password, mfa_code=self.mfa_code)
            logger.info("Successfully logged into Robinhood.")
        except Exception as e:
            logger.error(f"Failed to login to Robinhood: {e}")
            raise

    def logout(self):
        r.logout()
        logger.info("Logged out of Robinhood.")

    def get_account_profile(self):
        self.login()
        profile = r.build_user_profile()
        self.logout()
        return profile
