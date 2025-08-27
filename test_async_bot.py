#!/usr/bin/env python3
"""
🧪 Async Bot Testing & Validation Script
Test suite for the optimized async belmondo bot

Usage:
    python test_async_bot.py
"""

import asyncio
import time
import sys
import os
from unittest.mock import AsyncMock, MagicMock
from typing import Dict, Any

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (
    MessageProcessor, ContentSender, PlotinaManager,
    quote, get_horoscope, parse_message, client
)
from utils import sleep_choice_asyncio, clean_string
from if_rules import process_trigger_response, get_trigger_type


class AsyncBotTester:
    """Comprehensive async bot testing suite."""
    
    def __init__(self):
        self.tests_passed = 0
        self.tests_failed = 0
        self.test_results = []
    
    def log_test(self, test_name: str, passed: bool, details: str = ""):
        """Log test result."""
        status = "✅ PASS" if passed else "❌ FAIL"
        result = f"{status} {test_name}"
        if details:
            result += f" - {details}"
        
        print(result)
        self.test_results.append(result)
        
        if passed:
            self.tests_passed += 1
        else:
            self.tests_failed += 1
    
    async def test_async_sleep_functions(self):
        """Test async sleep functions performance."""
        print("\n🔬 Testing async sleep functions...")
        
        start_time = time.time()
        await sleep_choice_asyncio([0.1, 0.2])
        end_time = time.time()
        
        # Should complete within reasonable time
        execution_time = end_time - start_time
        passed = 0.1 <= execution_time <= 0.5
        
        self.log_test(
            "Async sleep functions", 
            passed, 
            f"Execution time: {execution_time:.3f}s"
        )
    
    async def test_openai_client_init(self):
        """Test OpenAI async client initialization."""
        print("\n🔬 Testing OpenAI async client...")
        
        try:
            # Test that client is AsyncOpenAI instance
            from openai import AsyncOpenAI
            passed = isinstance(client, AsyncOpenAI)
            
            self.log_test(
                "OpenAI async client initialization", 
                passed,
                f"Client type: {type(client).__name__}"
            )
        except Exception as e:
            self.log_test(
                "OpenAI async client initialization", 
                False, 
                f"Error: {e}"
            )
    
    async def test_message_processor_async(self):
        """Test MessageProcessor async methods."""
        print("\n🔬 Testing MessageProcessor async methods...")
        
        # Mock objects
        mock_update = MagicMock()
        mock_context = MagicMock()
        mock_context.bot = AsyncMock()
        
        # Mock message data
        mock_update.message.text = "test message"
        mock_update.message.from_user.id = 12345
        mock_update.effective_chat.id = 67890
        mock_update.message.message_id = 111
        
        try:
            processor = MessageProcessor({})
            
            # Test async method exists and is callable
            assert hasattr(processor, 'process_bot_messages')
            assert asyncio.iscoroutinefunction(processor.process_bot_messages)
            
            self.log_test(
                "MessageProcessor async methods", 
                True,
                "All methods are properly async"
            )
        except Exception as e:
            self.log_test(
                "MessageProcessor async methods", 
                False, 
                f"Error: {e}"
            )
    
    async def test_content_sender_async(self):
        """Test ContentSender async methods."""
        print("\n🔬 Testing ContentSender async methods...")
        
        try:
            # Test that methods are async
            assert asyncio.iscoroutinefunction(ContentSender.send_oxxxy)
            assert asyncio.iscoroutinefunction(ContentSender.send_goblin)
            assert asyncio.iscoroutinefunction(ContentSender.show_day)
            
            self.log_test(
                "ContentSender async methods", 
                True,
                "All static methods are properly async"
            )
        except Exception as e:
            self.log_test(
                "ContentSender async methods", 
                False, 
                f"Error: {e}"
            )
    
    async def test_plotina_manager_async(self):
        """Test PlotinaManager async methods."""
        print("\n🔬 Testing PlotinaManager async methods...")
        
        try:
            manager = PlotinaManager()
            
            # Test that methods are async
            assert asyncio.iscoroutinefunction(manager.build_plotina)
            assert asyncio.iscoroutinefunction(manager.show_stats)
            
            self.log_test(
                "PlotinaManager async methods", 
                True,
                "All methods are properly async"
            )
        except Exception as e:
            self.log_test(
                "PlotinaManager async methods", 
                False, 
                f"Error: {e}"
            )
    
    async def test_handler_functions_async(self):
        """Test that all handler functions are async."""
        print("\n🔬 Testing handler functions...")
        
        try:
            # Test key handler functions
            handlers_to_test = [
                quote, get_horoscope, parse_message
            ]
            
            for handler in handlers_to_test:
                assert asyncio.iscoroutinefunction(handler), f"{handler.__name__} is not async"
            
            self.log_test(
                "Handler functions async", 
                True,
                f"Tested {len(handlers_to_test)} handler functions"
            )
        except Exception as e:
            self.log_test(
                "Handler functions async", 
                False, 
                f"Error: {e}"
            )
    
    async def test_trigger_processing_async(self):
        """Test trigger processing functions."""
        print("\n🔬 Testing trigger processing...")
        
        try:
            # Test trigger processing functions
            assert asyncio.iscoroutinefunction(process_trigger_response)
            
            # Test trigger type detection (sync function)
            trigger_type = get_trigger_type("img:test.jpg")
            assert trigger_type == "image"
            
            trigger_type = get_trigger_type("regular text")
            assert trigger_type == "text"
            
            self.log_test(
                "Trigger processing async", 
                True,
                "Trigger functions working correctly"
            )
        except Exception as e:
            self.log_test(
                "Trigger processing async", 
                False, 
                f"Error: {e}"
            )
    
    async def test_utility_functions(self):
        """Test utility functions."""
        print("\n🔬 Testing utility functions...")
        
        try:
            # Test string cleaning
            cleaned = clean_string("Hello, World!")
            assert cleaned == "Hello World", f"Expected 'Hello World', got '{cleaned}'"
            
            self.log_test(
                "Utility functions", 
                True,
                "String cleaning works correctly"
            )
        except Exception as e:
            self.log_test(
                "Utility functions", 
                False, 
                f"Error: {e}"
            )
    
    async def test_imports_and_compatibility(self):
        """Test that all imports work correctly."""
        print("\n🔬 Testing imports and compatibility...")
        
        try:
            # Test modern telegram imports
            from telegram.ext import Application, ContextTypes, filters
            from telegram import Update
            
            # Test async libraries
            import aiofiles
            import asyncio
            
            # Test OpenAI async
            from openai import AsyncOpenAI
            
            self.log_test(
                "Imports and compatibility", 
                True,
                "All modern async imports successful"
            )
        except ImportError as e:
            self.log_test(
                "Imports and compatibility", 
                False, 
                f"Import error: {e}"
            )
    
    async def run_all_tests(self):
        """Run all async bot tests."""
        print("🚀 Starting Async Bot Validation Tests...\n")
        
        test_methods = [
            self.test_imports_and_compatibility,
            self.test_async_sleep_functions,
            self.test_openai_client_init,
            self.test_message_processor_async,
            self.test_content_sender_async,
            self.test_plotina_manager_async,
            self.test_handler_functions_async,
            self.test_trigger_processing_async,
            self.test_utility_functions,
        ]
        
        for test_method in test_methods:
            try:
                await test_method()
            except Exception as e:
                self.log_test(
                    test_method.__name__, 
                    False, 
                    f"Unexpected error: {e}"
                )
        
        # Print summary
        total_tests = self.tests_passed + self.tests_failed
        print(f"\n📊 Test Summary:")
        print(f"   Total tests: {total_tests}")
        print(f"   ✅ Passed: {self.tests_passed}")
        print(f"   ❌ Failed: {self.tests_failed}")
        print(f"   Success rate: {(self.tests_passed/total_tests)*100:.1f}%")
        
        if self.tests_failed == 0:
            print("\n🎉 All tests passed! The async optimization is working correctly.")
        else:
            print(f"\n⚠️  {self.tests_failed} tests failed. Please review the errors above.")
        
        return self.tests_failed == 0


async def main():
    """Run the async bot validation tests."""
    tester = AsyncBotTester()
    success = await tester.run_all_tests()
    
    if success:
        print("\n✨ Async optimization validation complete - ALL SYSTEMS GO! ✨")
        return 0
    else:
        print("\n❌ Some tests failed - please review and fix issues before deployment.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)