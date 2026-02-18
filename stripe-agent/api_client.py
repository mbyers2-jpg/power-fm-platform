"""
Stripe API Client
Handles authentication, rate limiting, pagination, and error handling.
Uses form-encoded POST bodies for create operations (Stripe convention).
"""

import os
import json
import time
import logging
import requests
from datetime import datetime

log = logging.getLogger('stripe-agent')

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config', 'stripe_config.json')
BASE_URL = 'https://api.stripe.com/v1'
STRIPE_API_VERSION = '2024-12-18.acacia'


class StripeClient:
    """Stripe API client with pagination and rate limiting."""

    def __init__(self):
        self.config = self._load_config()
        self.secret_key = self.config.get('secret_key', '')
        self.webhook_secret = self.config.get('webhook_secret', '')
        self.session = requests.Session()
        self.session.auth = (self.secret_key, '')
        self.session.headers.update({
            'Stripe-Version': STRIPE_API_VERSION,
        })
        self.request_count = 0

    def _load_config(self):
        if not os.path.exists(CONFIG_PATH):
            log.warning(f"Config not found: {CONFIG_PATH}")
            return {}
        with open(CONFIG_PATH) as f:
            return json.load(f)

    def _request(self, method, endpoint, data=None, params=None):
        """Make an API request with rate limiting and error handling."""
        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        max_retries = 3

        for attempt in range(max_retries):
            try:
                resp = self.session.request(
                    method, url, data=data, params=params, timeout=30
                )
                self.request_count += 1

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get('Retry-After', 2))
                    log.warning(f"Rate limited. Retrying in {retry_after}s...")
                    time.sleep(retry_after)
                    continue

                if resp.status_code == 401:
                    log.error("Authentication failed. Check your secret_key in config.")
                    return None

                if resp.status_code >= 400:
                    error = resp.json().get('error', {})
                    log.error(f"Stripe error ({resp.status_code}): {error.get('message', resp.text)}")
                    return None

                return resp.json()

            except requests.exceptions.Timeout:
                log.warning(f"Request timeout (attempt {attempt + 1}/{max_retries})")
                time.sleep(2 ** attempt)
            except requests.exceptions.RequestException as e:
                log.error(f"Request failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    return None
        return None

    def _paginate(self, endpoint, params=None, limit=None):
        """Auto-paginate through all results."""
        if params is None:
            params = {}
        params.setdefault('limit', 100)
        all_data = []

        while True:
            result = self._request('GET', endpoint, params=params)
            if not result or 'data' not in result:
                break

            all_data.extend(result['data'])

            if limit and len(all_data) >= limit:
                return all_data[:limit]

            if not result.get('has_more', False):
                break

            params['starting_after'] = result['data'][-1]['id']

        return all_data

    # --- Customers ---

    def list_customers(self, limit=None):
        """List all customers."""
        return self._paginate('customers', limit=limit)

    def get_customer(self, customer_id):
        """Get a single customer."""
        return self._request('GET', f'customers/{customer_id}')

    def create_customer(self, email, name=None, metadata=None):
        """Create a new customer."""
        data = {'email': email}
        if name:
            data['name'] = name
        if metadata:
            for k, v in metadata.items():
                data[f'metadata[{k}]'] = v
        return self._request('POST', 'customers', data=data)

    def attach_test_payment_method(self, customer_id):
        """Attach Stripe's test payment method to a customer (test mode only)."""
        pm = self._request('POST', 'payment_methods', data={
            'type': 'card',
            'card[token]': 'tok_visa',
        })
        if not pm:
            return None
        self._request('POST', f'payment_methods/{pm["id"]}/attach', data={
            'customer': customer_id,
        })
        # Set as default payment method
        self._request('POST', f'customers/{customer_id}', data={
            'invoice_settings[default_payment_method]': pm['id'],
        })
        return pm

    # --- Subscriptions ---

    def list_subscriptions(self, status=None, limit=None):
        """List subscriptions, optionally filtered by status."""
        params = {}
        if status:
            params['status'] = status
        return self._paginate('subscriptions', params=params, limit=limit)

    def get_subscription(self, sub_id):
        """Get a single subscription."""
        return self._request('GET', f'subscriptions/{sub_id}')

    def create_subscription(self, customer_id, price_id, trial_days=None):
        """Create a subscription for a customer."""
        data = {
            'customer': customer_id,
            'items[0][price]': price_id,
        }
        if trial_days:
            data['trial_period_days'] = trial_days
        return self._request('POST', 'subscriptions', data=data)

    # --- Payments/Charges ---

    def list_payments(self, limit=None):
        """List payment intents."""
        return self._paginate('charges', limit=limit)

    # --- Products & Prices ---

    def list_products(self, limit=None):
        """List all products."""
        return self._paginate('products', limit=limit)

    def list_prices(self, product_id=None, limit=None):
        """List prices, optionally filtered by product."""
        params = {}
        if product_id:
            params['product'] = product_id
        return self._paginate('prices', params=params, limit=limit)

    def create_product(self, name, description=None):
        """Create a new product."""
        data = {'name': name}
        if description:
            data['description'] = description
        return self._request('POST', 'products', data=data)

    def create_price(self, product_id, unit_amount, currency='usd', interval='month'):
        """Create a recurring price for a product. unit_amount in cents."""
        data = {
            'product': product_id,
            'unit_amount': unit_amount,
            'currency': currency,
            'recurring[interval]': interval,
        }
        return self._request('POST', 'prices', data=data)

    # --- Checkout ---

    def create_checkout_session(self, price_id, success_url, cancel_url):
        """Create a Stripe Checkout session for a subscription with card + ACH bank payments."""
        data = {
            'mode': 'subscription',
            'line_items[0][price]': price_id,
            'line_items[0][quantity]': 1,
            'success_url': success_url,
            'cancel_url': cancel_url,
            'payment_method_types[0]': 'card',
            'payment_method_types[1]': 'us_bank_account',
            'payment_method_options[us_bank_account][financial_connections][permissions][0]': 'payment_method',
        }
        return self._request('POST', 'checkout/sessions', data=data)

    def create_one_time_checkout(self, product_name, amount_cents, success_url, cancel_url, metadata=None, customer_id=None):
        """Create a one-time payment Checkout session with inline price_data.
        When customer_id is provided, attaches payment to that customer.
        Always sets setup_future_usage so the card is saved for repeat purchases."""
        data = {
            'mode': 'payment',
            'line_items[0][price_data][currency]': 'usd',
            'line_items[0][price_data][product_data][name]': product_name,
            'line_items[0][price_data][unit_amount]': amount_cents,
            'line_items[0][quantity]': 1,
            'success_url': success_url,
            'cancel_url': cancel_url,
            'payment_method_types[0]': 'card',
            'payment_intent_data[setup_future_usage]': 'on_session',
        }
        if customer_id:
            data['customer'] = customer_id
        if metadata:
            for k, v in metadata.items():
                data[f'metadata[{k}]'] = v
        return self._request('POST', 'checkout/sessions', data=data)

    def get_checkout_session(self, session_id):
        """Retrieve a Checkout session by ID for payment verification."""
        return self._request('GET', f'checkout/sessions/{session_id}')

    # --- Payment Methods ---

    def list_payment_methods(self, customer_id):
        """List saved card payment methods for a customer."""
        return self._request('GET', 'payment_methods', params={
            'customer': customer_id,
            'type': 'card',
        })

    # --- Payment Intents ---

    def create_payment_intent(self, amount_cents, customer_id, payment_method_id, description=None, metadata=None):
        """Create and immediately confirm a PaymentIntent using a saved payment method."""
        data = {
            'amount': amount_cents,
            'currency': 'usd',
            'customer': customer_id,
            'payment_method': payment_method_id,
            'confirm': 'true',
            'off_session': 'true',
        }
        if description:
            data['description'] = description
        if metadata:
            for k, v in metadata.items():
                data[f'metadata[{k}]'] = v
        return self._request('POST', 'payment_intents', data=data)

    def get_payment_intent(self, payment_intent_id):
        """Retrieve a PaymentIntent by ID for verification."""
        return self._request('GET', f'payment_intents/{payment_intent_id}')

    # --- Invoices ---

    def list_invoices(self, limit=None):
        """List all invoices."""
        return self._paginate('invoices', limit=limit)

    # --- Balance ---

    def get_balance(self):
        """Get current Stripe balance."""
        return self._request('GET', 'balance')

    def is_configured(self):
        """Check if API credentials are configured."""
        return bool(self.secret_key)
