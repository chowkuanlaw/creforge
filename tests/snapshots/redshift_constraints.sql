
CREATE SCHEMA IF NOT EXISTS creforge;

CREATE TABLE creforge.subject (
    subject_id VARCHAR(13) NOT NULL,
    birth_year SMALLINT NOT NULL,
    region VARCHAR(16) NOT NULL,
    income_band VARCHAR(32) NOT NULL,
    risk_grade VARCHAR(32) NOT NULL,
    created_month DATE NOT NULL,
    PRIMARY KEY (subject_id)
);

CREATE TABLE creforge.inquiry (
    inquiry_id VARCHAR(13) NOT NULL,
    subject_id VARCHAR(13) NOT NULL,
    inquiry_date DATE NOT NULL,
    lender_id VARCHAR(16) NOT NULL,
    product_type VARCHAR(32) NOT NULL,
    requested_amount DECIMAL(18,2) NOT NULL,
    outcome VARCHAR(32) NOT NULL,
    PRIMARY KEY (inquiry_id),
    FOREIGN KEY (subject_id) REFERENCES creforge.subject (subject_id)
);

CREATE TABLE creforge.account (
    account_id VARCHAR(13) NOT NULL,
    subject_id VARCHAR(13) NOT NULL,
    inquiry_id VARCHAR(13),
    lender_id VARCHAR(16) NOT NULL,
    product_type VARCHAR(32) NOT NULL,
    open_date DATE NOT NULL,
    credit_limit DECIMAL(18,2),
    principal DECIMAL(18,2),
    tenor_months SMALLINT,
    interest_rate DOUBLE PRECISION NOT NULL,
    secured BOOLEAN NOT NULL,
    close_date DATE,
    close_reason VARCHAR(32),
    PRIMARY KEY (account_id),
    FOREIGN KEY (subject_id) REFERENCES creforge.subject (subject_id),
    FOREIGN KEY (inquiry_id) REFERENCES creforge.inquiry (inquiry_id)
);

CREATE TABLE creforge.account_month (
    account_id VARCHAR(13) NOT NULL,
    as_of_month DATE NOT NULL,
    balance DECIMAL(18,2) NOT NULL,
    amount_due DECIMAL(18,2) NOT NULL,
    amount_paid DECIMAL(18,2) NOT NULL,
    dpd_bucket VARCHAR(32) NOT NULL,
    months_in_arrears SMALLINT NOT NULL,
    status VARCHAR(32) NOT NULL,
    PRIMARY KEY (account_id, as_of_month),
    FOREIGN KEY (account_id) REFERENCES creforge.account (account_id)
);

CREATE TABLE creforge.account_party (
    account_id VARCHAR(13) NOT NULL,
    subject_id VARCHAR(13) NOT NULL,
    role VARCHAR(32) NOT NULL,
    start_date DATE NOT NULL,
    PRIMARY KEY (account_id, subject_id),
    FOREIGN KEY (account_id) REFERENCES creforge.account (account_id),
    FOREIGN KEY (subject_id) REFERENCES creforge.subject (subject_id)
);
