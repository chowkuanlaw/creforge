select
    am.as_of_month,
    a.product_type,
    count(*) as account_months,
    count(distinct am.account_id) as active_accounts,
    sum(am.balance) as total_balance
from {{ source('creforge', 'account_month') }} as am
join {{ source('creforge', 'account') }} as a using (account_id)
group by am.as_of_month, a.product_type
