export const formatCurrency = (value: number | null | undefined): string => {
    if (value === null || value === undefined) return '₹0';

    const absValue = Math.abs(value);
    const isNegative = value < 0;
    const prefix = isNegative ? '-' : '';

    // < 1 Cr (10,000,000)
    if (absValue < 10000000) {
        return new Intl.NumberFormat('en-IN', {
            style: 'currency',
            currency: 'INR',
            maximumFractionDigits: 0
        }).format(value);
    }

    // 1 Cr to 100 Cr (1,000,000,000)
    if (absValue < 1000000000) {
        const crValue = absValue / 10000000;
        return `${prefix}₹${crValue.toFixed(2)} Cr`;
    }

    // 100+ Cr
    const crValue = absValue / 10000000;
    return `${prefix}₹${Math.round(crValue).toLocaleString()} Cr`;
};

export const formatNumber = (num: number | null | undefined): string => {
    if (num === null || num === undefined) return '0';
    return num.toLocaleString('en-IN');
};
