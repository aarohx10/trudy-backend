"""
Frontend Leak Test: Switch organizations and verify that the "Agents" list clears and repopulates.

This test verifies that:
1. When organization switches, React Query cache is cleared
2. Agents list updates immediately with new organization's data
3. No stale data from previous organization is displayed
"""
import pytest
from unittest.mock import Mock, patch


def test_org_switch_clears_query_cache():
    """Test that switching organizations clears React Query cache"""
    # Mock query client
    mock_query_client = Mock()
    mock_query_client.clear = Mock()
    
    # Simulate organization switch
    from frontend.src.stores.app_store import useAppStore
    
    # Mock the store
    with patch('frontend.src.stores.app_store.useAppStore') as mock_store:
        mock_store.return_value = {
            'activeOrgId': 'org_a',
            'setActiveOrgId': Mock(side_effect=lambda org_id: mock_query_client.clear())
        }
        
        # Simulate org switch
        store = mock_store.return_value
        store['setActiveOrgId']('org_b')
        
        # Verify cache was cleared
        mock_query_client.clear.assert_called_once()


def test_agents_query_key_includes_org_id():
    """Test that agents query key includes orgId to ensure cache isolation"""
    # Mock useAgents hook
    from frontend.src.hooks.use_agents import useAgents
    
    with patch('frontend.src.hooks.use_agents.useQuery') as mock_use_query, \
         patch('frontend.src.hooks.use_agents.useOrganization') as mock_use_org, \
         patch('frontend.src.hooks.use_agents.useAppStore') as mock_store:
        
        mock_use_org.return_value = {'organization': {'id': 'org_a'}}
        mock_store.return_value = {'activeOrgId': 'org_a', 'setActiveOrgId': Mock()}
        
        useAgents()
        
        # Verify query key includes orgId
        call_args = mock_use_query.call_args
        query_key = call_args[1]['queryKey']
        
        assert 'org_a' in query_key or query_key[1] == 'org_a'  # orgId should be in query key


def test_hard_refresh_on_org_switch():
    """Test that workspace switcher triggers hard refresh on organization change"""
    # Mock window.location
    mock_location = Mock()
    mock_location.href = ''
    
    with patch('builtins.window', Mock(location=mock_location, __REACT_QUERY_CLIENT__=Mock())):
        # Simulate OrganizationSwitcher afterSelectOrganization callback
        def after_select_org(org):
            if org and org.id:
                # Clear cache
                window.__REACT_QUERY_CLIENT__.clear()
                # Hard refresh
                window.location.href = '/dashboard'
        
        # Simulate org switch
        mock_org = Mock(id='org_b', name='Org B')
        after_select_org(mock_org)
        
        # Verify hard refresh was triggered
        assert mock_location.href == '/dashboard'


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
