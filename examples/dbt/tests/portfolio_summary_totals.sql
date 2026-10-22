with source_rows as (
    select count(*) as n from {{ source('creforge', 'account_month') }}
), summary_rows as (
    select coalesce(sum(account_months), 0) as n from {{ ref('portfolio_summary') }}
)
select source_rows.n as source_count, summary_rows.n as summary_count
from source_rows cross join summary_rows
where source_rows.n <> summary_rows.n
