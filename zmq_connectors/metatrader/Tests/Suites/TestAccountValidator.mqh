//+------------------------------------------------------------------+
//|                         Tests/Suites/TestAccountValidator.mqh    |
//|  Suite: AccountValidator accepts only the terminal's own login   |
//|  (driven by FakeAccountApi).                                     |
//+------------------------------------------------------------------+
#property strict

#include "../Framework/TestFramework.mqh"
#include "../Fakes/Fakes.mqh"
#include "../../Domain/AccountValidator.mqh"

//+------------------------------------------------------------------+
//| RunAccountValidatorTests                                         |
//+------------------------------------------------------------------+
void RunAccountValidatorTests()
{
   //--- Matching account is accepted and echoed back
   {
      FakeAccountApi account;
      account.SetLogin(9876543);

      string resolved = "";
      bool ok = AccountValidator::Validate(&account, "9876543", resolved);
      AssertTrue(ok, "AccountValidator: matching login accepted");
      AssertEqualString("9876543", resolved, "AccountValidator: resolved echoes login");
   }

   //--- Foreign account is rejected
   {
      FakeAccountApi account;
      account.SetLogin(9876543);

      string resolved = "";
      bool ok = AccountValidator::Validate(&account, "1111111", resolved);
      AssertFalse(ok, "AccountValidator: foreign login rejected");
      AssertEqualString("9876543", resolved, "AccountValidator: resolved still echoes login");
   }

   //--- Empty account is rejected (app always sends one)
   {
      FakeAccountApi account;
      account.SetLogin(9876543);

      string resolved = "";
      bool ok = AccountValidator::Validate(&account, "", resolved);
      AssertFalse(ok, "AccountValidator: empty account rejected");
   }
}
