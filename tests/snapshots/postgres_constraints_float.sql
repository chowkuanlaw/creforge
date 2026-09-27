
CREATE TABLE subject (
    subject_id VARCHAR(13) NOT NULL,
    birth_year SMALLINT NOT NULL,
    region VARCHAR(16) NOT NULL,
    income_band VARCHAR(32) NOT NULL CHECK (income_band IN ('B1', 'B2', 'B3', 'B4', 'B5', 'B6')),
    risk_grade VARCHAR(32) NOT NULL CHECK (risk_grade IN ('A', 'B', 'C', 'D', 'E')),
    created_month DATE NOT NULL,
    PRIMARY KEY (subject_id)
);

CREATE TABLE inquiry (
    inquiry_id VARCHAR(13) NOT NULL,
    subject_id VARCHAR(13) NOT NULL,
    inquiry_date DATE NOT NULL,
    lender_id VARCHAR(16) NOT NULL,
    product_type VARCHAR(32) NOT NULL CHECK (product_type IN ('credit_card', 'personal_loan', 'mortgage', 'auto_loan', 'overdraft', 'bnpl')),
    requested_amount DOUBLE PRECISION NOT NULL,
    outcome VARCHAR(32) NOT NULL CHECK (outcome IN ('approved', 'declined', 'withdrawn')),
    PRIMARY KEY (inquiry_id),
    FOREIGN KEY (subject_id) REFERENCES subject (subject_id)
);

CREATE TABLE account (
    account_id VARCHAR(13) NOT NULL,
    subject_id VARCHAR(13) NOT NULL,
    inquiry_id VARCHAR(13),
    lender_id VARCHAR(16) NOT NULL,
    product_type VARCHAR(32) NOT NULL CHECK (product_type IN ('credit_card', 'personal_loan', 'mortgage', 'auto_loan', 'overdraft', 'bnpl')),
    open_date DATE NOT NULL,
    credit_limit DOUBLE PRECISION,
    principal DOUBLE PRECISION,
    tenor_months SMALLINT,
    interest_rate DOUBLE PRECISION NOT NULL,
    secured BOOLEAN NOT NULL,
    close_date DATE,
    close_reason VARCHAR(32) CHECK (close_reason IN ('paid_off', 'written_off', 'closed_by_customer', 'refinanced')),
    PRIMARY KEY (account_id),
    FOREIGN KEY (subject_id) REFERENCES subject (subject_id),
    FOREIGN KEY (inquiry_id) REFERENCES inquiry (inquiry_id)
);

CREATE TABLE account_month (
    account_id VARCHAR(13) NOT NULL,
    as_of_month DATE NOT NULL,
    balance DOUBLE PRECISION NOT NULL,
    amount_due DOUBLE PRECISION NOT NULL,
    amount_paid DOUBLE PRECISION NOT NULL,
    dpd_bucket VARCHAR(32) NOT NULL CHECK (dpd_bucket IN ('0', '1-29', '30-59', '60-89', '90-119', '120+')),
    months_in_arrears SMALLINT NOT NULL,
    status VARCHAR(32) NOT NULL CHECK (status IN ('current', 'delinquent', 'restructured', 'written_off', 'closed')),
    PRIMARY KEY (account_id, as_of_month),
    FOREIGN KEY (account_id) REFERENCES account (account_id)
);

CREATE TABLE account_party (
    account_id VARCHAR(13) NOT NULL,
    subject_id VARCHAR(13) NOT NULL,
    role VARCHAR(32) NOT NULL CHECK (role IN ('primary', 'joint', 'guarantor')),
    start_date DATE NOT NULL,
    PRIMARY KEY (account_id, subject_id),
    FOREIGN KEY (account_id) REFERENCES account (account_id),
    FOREIGN KEY (subject_id) REFERENCES subject (subject_id)
);
