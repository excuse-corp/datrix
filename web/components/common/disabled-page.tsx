import Error from 'next/error';

function DisabledPage(_props: any) {
  return <Error statusCode={404} />;
}

export default DisabledPage;
