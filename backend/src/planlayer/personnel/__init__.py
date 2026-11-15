"""Personnel costs (ADR 0022): each position's pay by month, the yearly extras, raises, awards,
bonus and contributions of the round's rules, written as totals to the personnel accounts of
the cost center charged. Pay is read by HR and the cost center's owner only (``pay_access``).

The package's modules build on each other in this order: ``common``, ``costs``, ``pay``,
``rules``."""

from planlayer.personnel.common import module_on as module_on
from planlayer.personnel.costs import recompute as recompute
from planlayer.personnel.costs import small_groups as small_groups
from planlayer.personnel.pay import PayIn as PayIn
from planlayer.personnel.pay import PayPage as PayPage
from planlayer.personnel.pay import PayUnit as PayUnit
from planlayer.personnel.pay import confirm as confirm
from planlayer.personnel.pay import needs_review as needs_review
from planlayer.personnel.pay import read_page as read_page
from planlayer.personnel.pay import save_pay as save_pay
from planlayer.personnel.pay import take_hr_salaries as take_hr_salaries
from planlayer.personnel.pay import units as units
from planlayer.personnel.rules import RuleIO as RuleIO
from planlayer.personnel.rules import RulesIO as RulesIO
from planlayer.personnel.rules import RulesPage as RulesPage
from planlayer.personnel.rules import StaffTypeIO as StaffTypeIO
from planlayer.personnel.rules import StepIO as StepIO
from planlayer.personnel.rules import carry as carry
from planlayer.personnel.rules import read_rules as read_rules
from planlayer.personnel.rules import save_rules as save_rules
from planlayer.personnel.rules import switch as switch
