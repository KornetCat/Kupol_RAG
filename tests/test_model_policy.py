import os
import unittest
from unittest.mock import patch

from kupol_agent.llm import PlannerError
from kupol_agent.model_policy import ModelPolicy


class ModelPolicyTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'DEEPSEEK_MODEL': '', 'DEEPSEEK_BASE_URL': '',
            'DEEPSEEK_FINAL_MODEL': '', 'DEEPSEEK_FINAL_SERVED_MODEL': '',
            'DEEPSEEK_ORGANIZER_ENDPOINT_APPROVED': ''})
        environment.start()
        self.addCleanup(environment.stop)

    def test_both_profiles_use_available_deepseek_without_version_gate(self):
        for profile in ('training', 'competition'):
            policy = ModelPolicy(profile)
            policy.ensure_ready()
            self.assertEqual(policy.model, 'deepseek-flash')
            policy.validate_response('deepseek-flash')
            policy.validate_response('DeepSeek-V4.1-Flash-1017')
            status = policy.public_status()
            self.assertTrue(status['ready'])
            self.assertEqual(status['required_family'], 'DeepSeek')
            self.assertFalse(status['version_pinned'])
            self.assertIsNone(status['expected_served_model'])

    def test_explicit_legacy_model_id_does_not_force_old_family(self):
        policy = ModelPolicy('competition', final_model='deepseek-v4-flash')
        policy.ensure_ready()
        self.assertEqual(policy.model, 'deepseek-v4-flash')
        policy.validate_response('DeepSeek-V4.1-Flash-1017')

    def test_exact_served_model_remains_optional_for_approved_organizer_deployment(self):
        policy = ModelPolicy("competition", base_url="https://organizer.invalid/deepseek", final_model="deepseek-v4-flash",
                             final_served_model="DeepSeek-V4-Flash", organizer_endpoint_approved=True)
        policy.ensure_ready()
        policy.validate_response("DeepSeek-V4-Flash")
        for model in ("DeepSeek-V4.1-Flash-1017", "DeepSeek-V4-Pro", None):
            with self.assertRaises(PlannerError):
                policy.validate_response(model)

    def test_optional_served_pin_accepts_any_deepseek_family_in_both_profiles(self):
        for profile in ('training', 'competition'):
            for served in ('DeepSeek-V4.1-Flash-1017', 'DeepSeek-V4-Pro', 'deepseek-flash'):
                policy = ModelPolicy(profile, final_served_model=served)
                policy.ensure_ready()
                policy.validate_response(served.upper())
                self.assertTrue(policy.public_status()['version_pinned'])
                with self.assertRaises(PlannerError):
                    policy.validate_response('deepseek-different-model')

    def test_non_deepseek_request_or_response_rejected_in_both_profiles(self):
        for profile in ('training', 'competition'):
            for model in ('gpt-other', 'claude-other', 'mistral'):
                with self.assertRaises(PlannerError):
                    ModelPolicy(profile, model=model).ensure_ready()
            for served in ('gpt-other', 'claude-other', None, 42, {'model': 'deepseek-flash'}):
                with self.assertRaises(PlannerError):
                    ModelPolicy(profile).validate_response(served)

    def test_invalid_optional_pin_fails_closed(self):
        for served in ('gpt-other', False, 42, {}):
            self.assertIsNotNone(ModelPolicy('competition', final_served_model=served).readiness_error())

    def test_unapproved_or_malformed_endpoint_is_never_ready(self):
        for endpoint in ('http://api.deepseek.com', 'https://user:key@api.deepseek.com',
                         'https://other.invalid', 'https://api.deepseek.com?token=x', 'https://api.deepseek.com:444'):
            self.assertIsNotNone(ModelPolicy('training', base_url=endpoint, organizer_endpoint_approved=False).readiness_error())


if __name__ == "__main__":
    unittest.main()
